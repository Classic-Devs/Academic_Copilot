import json
import logging
import mimetypes

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from .ai_service import Retrieval, confidence_label, friendly_error, get_ai_service
from .models import AcademicDocument, ChatMessage, DocumentChunk, UserProfile
from .processor import enqueue_document, file_extension, is_active, recover_stale_documents
from .security import UploadRejected, clean_title, client_ip, rate_limited, user_or_ip, validate_upload

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 4000
CHAT_HISTORY_LIMIT = 50
RESOURCES_PER_PAGE = 24


def json_error(message, status=400):
    return JsonResponse({'status': 'error', 'message': message}, status=status)


def too_many_requests():
    return json_error('Too many requests. Please slow down and try again shortly.', status=429)


# ── Forms ────────────────────────────────────────────────────────────────────

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, help_text="Required. Enter a valid email address.")

    class Meta(UserCreationForm.Meta):
        fields = UserCreationForm.Meta.fields + ('email',)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class ProfileForm(forms.Form):
    EDUCATION_LEVELS = ['', 'High School', 'Undergraduate', 'Postgraduate', 'PhD', 'Other']

    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    secondary_email = forms.EmailField(required=False)
    education_level = forms.CharField(max_length=100, required=False)
    bio = forms.CharField(max_length=2000, required=False)


# ── Public pages & auth ──────────────────────────────────────────────────────

class LandingView(View):
    def get(self, request):
        return render(request, 'landing.html')


class SignupView(View):
    def get(self, request):
        return render(request, 'signup.html', {'form': CustomUserCreationForm()})

    def post(self, request):
        form = CustomUserCreationForm(request.POST)
        if rate_limited('signup', client_ip(request)):
            form.add_error(None, "Too many sign-up attempts. Please try again later.")
            return render(request, 'signup.html', {'form': form}, status=429)
        if form.is_valid():
            user = form.save()
            UserProfile.objects.get_or_create(user=user)
            login(request, user)
            messages.success(request, "Account created successfully!")
            return redirect('dashboard')
        return render(request, 'signup.html', {'form': form})


class LoginView(View):
    def get(self, request):
        return render(request, 'login.html', {'form': AuthenticationForm(), 'next': request.GET.get('next', '')})

    def post(self, request):
        form = AuthenticationForm(request, data=request.POST)
        next_url = request.POST.get('next', '')
        username = request.POST.get('username', '').lower()
        if rate_limited('login', f"{client_ip(request)}:{username}"):
            form.errors.clear()
            form.add_error(None, "Too many login attempts. Please wait a few minutes and try again.")
            return render(request, 'login.html', {'form': form, 'next': next_url}, status=429)
        if form.is_valid():
            login(request, form.get_user())
            if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                return redirect(next_url)
            return redirect('dashboard')
        return render(request, 'login.html', {'form': form, 'next': next_url})


class LogoutView(View):
    http_method_names = ['post']

    def post(self, request):
        logout(request)
        return redirect('landing')


class ProfileView(LoginRequiredMixin, View):
    def get(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        return render(request, 'profile.html', {'profile': profile})

    def post(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        form = ProfileForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                messages.error(request, errors[0])
            return render(request, 'profile.html', {'profile': profile}, status=400)

        data = form.cleaned_data
        request.user.first_name = data['first_name'].strip()
        request.user.last_name = data['last_name'].strip()
        request.user.save(update_fields=['first_name', 'last_name'])

        profile.secondary_email = data['secondary_email'] or None
        profile.education_level = data['education_level'].strip() or None
        profile.bio = data['bio'].strip() or None
        profile.save()

        messages.success(request, "Profile updated successfully!")
        return redirect('user_profile')


# ── Library ──────────────────────────────────────────────────────────────────

class DashboardView(LoginRequiredMixin, View):
    def get(self, request):
        recover_stale_documents(user=request.user)
        docs = AcademicDocument.objects.filter(user=request.user).order_by('-uploaded_at')
        total_chunks = DocumentChunk.objects.filter(document__user=request.user).count()

        # Real retrieval quality: mean source similarity across recent answers.
        recent = list(
            ChatMessage.objects.filter(user=request.user, confidence__isnull=False)
            .order_by('-created_at').values_list('confidence', flat=True)[:CHAT_HISTORY_LIMIT]
        )
        ai_confidence = f"{round(100 * sum(recent) / len(recent))}%" if recent else "—"

        return render(request, 'dashboard.html', {
            'docs': docs,
            'total_docs': len(docs),
            'total_chunks': total_chunks,
            'ai_confidence': ai_confidence,
            'max_upload_mb': settings.MAX_UPLOAD_SIZE_MB,
            'processing_ids': json.dumps([d.pk for d in docs if d.status in ('processing', 'pending')]),
        })


class DocumentStatusView(LoginRequiredMixin, View):
    """Lightweight polling endpoint so the dashboard can update without reloads."""

    def get(self, request):
        docs = AcademicDocument.objects.filter(user=request.user).values('id', 'status', 'error_message')
        return JsonResponse({'documents': list(docs)})


class UploadView(LoginRequiredMixin, View):
    def post(self, request):
        if rate_limited('upload', user_or_ip(request)):
            return too_many_requests()
        uploaded = request.FILES.get('file')
        if not uploaded:
            return json_error('No file provided.')

        title = clean_title(uploaded.name)
        try:
            file_hash = validate_upload(uploaded)
        except UploadRejected as exc:
            return json_error(f'"{title}": {exc}')

        existing = AcademicDocument.objects.filter(user=request.user, file_hash=file_hash).first()
        if existing:
            return json_error(f'"{title}" is already in your library as "{existing.title}".')

        field_of_study = request.POST.get('field_of_study', '').strip()[:100] or 'General'
        is_public = request.POST.get('is_public', 'true').lower() in ('true', '1', 'yes', 'on')

        with transaction.atomic():
            doc = AcademicDocument.objects.create(
                user=request.user,
                title=title,
                file=uploaded,
                file_hash=file_hash,
                field_of_study=field_of_study,
                is_public=is_public,
                status='processing',
            )
            enqueue_document(doc.pk)
        return JsonResponse({'status': 'success', 'doc_id': doc.pk})


class ReprocessView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        doc = get_object_or_404(AcademicDocument, id=doc_id, user=request.user)
        if is_active(doc.pk):
            return json_error('This document is already being processed.', status=409)
        with transaction.atomic():
            AcademicDocument.objects.filter(pk=doc.pk).update(status='processing', error_message=None)
            enqueue_document(doc.pk)
        return JsonResponse({'status': 'success'})


class DeleteDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        doc = get_object_or_404(AcademicDocument, id=doc_id, user=request.user)
        doc_pk, file_name, storage = doc.pk, doc.file.name, doc.file.storage

        with transaction.atomic():
            doc.delete()  # chunks cascade
            file_shared = bool(file_name) and AcademicDocument.objects.filter(file=file_name).exists()

            def cleanup():
                try:
                    get_ai_service().vectors.delete_document(doc_pk)
                except Exception:
                    logger.exception("Failed to delete vectors for document %s", doc_pk)
                if file_name and not file_shared:
                    try:
                        storage.delete(file_name)
                    except Exception:
                        logger.exception("Failed to delete file for document %s", doc_pk)

            transaction.on_commit(cleanup)
        return JsonResponse({'status': 'success'})


class ToggleVisibilityView(LoginRequiredMixin, View):
    """Let an owner flip a document they uploaded between public and private."""

    def post(self, request, doc_id):
        doc = get_object_or_404(AcademicDocument, id=doc_id, user=request.user)
        if doc.original_uploader:
            return json_error("Resources added from the community library can't be re-shared.", status=403)
        doc.is_public = not doc.is_public
        doc.save(update_fields=['is_public', 'updated_at'])
        return JsonResponse({
            'status': 'success',
            'is_public': doc.is_public,
            'message': 'Resource is now public.' if doc.is_public else 'Resource is now private.',
        })


class DocumentFileView(LoginRequiredMixin, View):
    """Serve a document's file to its owner, or to anyone signed in if it is public."""

    CONTENT_TYPES = {'.pdf': 'application/pdf', '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8'}

    def get(self, request, doc_id):
        doc = get_object_or_404(AcademicDocument, id=doc_id)
        if doc.user_id != request.user.pk and not doc.is_public:
            raise Http404
        try:
            handle = doc.file.open('rb')
        except (FileNotFoundError, OSError):
            raise Http404
        ext = file_extension(doc.file.name)
        content_type = self.CONTENT_TYPES.get(ext) or mimetypes.guess_type(doc.title)[0] or 'application/octet-stream'
        response = FileResponse(handle, content_type=content_type, as_attachment=False, filename=doc.title)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, max-age=3600'
        return response


# ── Community library ────────────────────────────────────────────────────────

class GlobalResourcesView(LoginRequiredMixin, View):
    def get(self, request):
        query = request.GET.get('q', '').strip()[:200]
        field = request.GET.get('field', '').strip()[:100]

        public = AcademicDocument.objects.filter(is_public=True, status='ready')
        docs = public.exclude(user=request.user).select_related('user').order_by('-uploaded_at')
        if query:
            docs = docs.filter(title__icontains=query) | docs.filter(field_of_study__icontains=query)
        if field:
            docs = docs.filter(field_of_study__iexact=field)

        page = Paginator(docs, RESOURCES_PER_PAGE).get_page(request.GET.get('page'))
        fields_of_study = (
            public.exclude(field_of_study__isnull=True).exclude(field_of_study='')
            .order_by('field_of_study').values_list('field_of_study', flat=True).distinct()
        )
        owned_hashes = set(
            AcademicDocument.objects.filter(user=request.user).exclude(file_hash='')
            .values_list('file_hash', flat=True)
        )

        return render(request, 'resources.html', {
            'docs': page.object_list,
            'page_obj': page,
            'fields_of_study': list(fields_of_study),
            'current_field': field,
            'search_query': query,
            'owned_hashes': owned_hashes,
        })


class AcquireDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        if rate_limited('acquire', user_or_ip(request)):
            return too_many_requests()

        original = get_object_or_404(AcademicDocument.objects.select_related('user'), id=doc_id, is_public=True)
        if original.user_id == request.user.pk:
            return json_error('You cannot add your own resource to your library.')

        duplicate = AcademicDocument.objects.filter(user=request.user)
        duplicate = duplicate.filter(file_hash=original.file_hash) if original.file_hash else duplicate.filter(title=original.title)
        if duplicate.exists():
            return json_error(f'"{original.title}" is already in your library.')

        # The copy references the same stored file (no byte duplication) and its
        # index is cloned from the original's vectors, so no embedding calls run.
        with transaction.atomic():
            new_doc = AcademicDocument.objects.create(
                user=request.user,
                title=original.title,
                file=original.file.name,
                file_hash=original.file_hash,
                field_of_study=original.field_of_study,
                original_uploader=original.user.username if original.user else None,
                is_public=False,
                status='processing',
            )
            enqueue_document(new_doc.pk)

        return JsonResponse({
            'status': 'success',
            'message': f'"{original.title}" added to your library. AI processing started!',
        })


# ── Chat ─────────────────────────────────────────────────────────────────────

class ChatView(LoginRequiredMixin, View):
    def get(self, request):
        latest = ChatMessage.objects.filter(user=request.user).order_by('-created_at')[:CHAT_HISTORY_LIMIT]
        history = [
            {
                'id': msg.id,
                'query': msg.query,
                'response': msg.response,
                'citations': msg.citations,
                'confidence': msg.confidence,
                'confidence_label': confidence_label(msg.confidence),
                'created_at': msg.created_at.strftime("%b %d, %H:%M"),
            }
            for msg in reversed(list(latest))
        ]
        return render(request, 'chat.html', {'chat_history': history})

    def post(self, request):
        try:
            data = json.loads(request.body or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return json_error('Invalid request.')
        query = str(data.get('query', '')).strip()
        if not query:
            return json_error('Please enter a question.')
        if len(query) > MAX_QUERY_CHARS:
            return json_error(f'Questions are limited to {MAX_QUERY_CHARS} characters.')
        if rate_limited('chat', user_or_ip(request)):
            return too_many_requests()

        try:
            service = get_ai_service()
        except Exception as exc:
            logger.exception("AI service unavailable")
            return json_error(friendly_error(exc), status=503)

        context_available = True
        try:
            retrieval = service.retrieve(query, request.user)
        except Exception:
            logger.exception("Retrieval failed; answering without library context")
            retrieval, context_available = Retrieval(), False

        history = list(ChatMessage.objects.filter(user=request.user).order_by('-created_at')[:3])[::-1]
        response = StreamingHttpResponse(
            self._events(request.user, query, retrieval, history, service, context_available),
            content_type='application/x-ndjson',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response

    @staticmethod
    def _events(user, query, retrieval, history, service, context_available):
        def event(payload):
            return json.dumps(payload) + '\n'

        citations = retrieval.citations()
        confidence = retrieval.confidence
        yield event({
            'type': 'meta',
            'citations': citations,
            'confidence': confidence,
            'confidence_label': confidence_label(confidence),
            'context_available': context_available,
        })

        parts = []
        try:
            for text in service.stream_answer(query, retrieval, history):
                parts.append(text)
                yield event({'type': 'delta', 'text': text})
        except Exception as exc:
            logger.exception("Chat generation failed")
            if not parts:
                yield event({'type': 'error', 'message': friendly_error(exc)})
                return
            parts.append("\n\n_[The response was interrupted. Please ask again for the rest.]_")
            yield event({'type': 'delta', 'text': parts[-1]})

        answer = ''.join(parts).strip()
        if not answer:
            yield event({'type': 'error', 'message': 'The AI returned an empty response. Please try rephrasing.'})
            return
        msg = ChatMessage.objects.create(
            user=user, query=query, response=answer, citations=citations, confidence=confidence
        )
        yield event({'type': 'done', 'id': msg.id})


class DeleteChatMessageView(LoginRequiredMixin, View):
    def post(self, request, msg_id):
        msg = get_object_or_404(ChatMessage, id=msg_id, user=request.user)
        msg.delete()
        return JsonResponse({'status': 'success'})


class ClearChatView(LoginRequiredMixin, View):
    def post(self, request):
        ChatMessage.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'success'})
