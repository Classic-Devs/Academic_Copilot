import os
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.http import JsonResponse
from .models import AcademicDocument, DocumentChunk, UserProfile, ChatMessage
from .ai_service import AIService
from django.db.models import Sum, Q
from django.contrib.auth import login, authenticate, logout
from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, help_text="Required. Enter a valid email address.")

    class Meta(UserCreationForm.Meta):
        fields = UserCreationForm.Meta.fields + ('email',)

class LandingView(View):
    def get(self, request):
        return render(request, 'landing.html')

class SignupView(View):
    def get(self, request):
        form = CustomUserCreationForm()
        return render(request, 'signup.html', {'form': form})

    def post(self, request):
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserProfile.objects.create(user=user)
            login(request, user)
            messages.success(request, "Account created successfully!")
            return redirect('dashboard')
        return render(request, 'signup.html', {'form': form})

class LoginView(View):
    def get(self, request):
        form = AuthenticationForm()
        return render(request, 'login.html', {'form': form})

    def post(self, request):
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('dashboard')
        return render(request, 'login.html', {'form': form})

class LogoutView(View):
    def get(self, request):
        logout(request)
        return redirect('landing')

class DashboardView(LoginRequiredMixin, View):
    def get(self, request):
        # Filter documents by the logged-in user
        docs = AcademicDocument.objects.filter(user=request.user).order_by('-uploaded_at')
        total_docs = docs.count()
        total_chunks = DocumentChunk.objects.filter(document__user=request.user).count()
        ai_confidence = "94%"

        context = {
            'docs': docs,
            'total_docs': total_docs,
            'total_chunks': total_chunks,
            'ai_confidence': ai_confidence,
        }
        return render(request, 'dashboard.html', context)

class ProfileView(LoginRequiredMixin, View):
    def get(self, request):
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        return render(request, 'profile.html', {'profile': profile})

    def post(self, request):
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        # Handle profile updates securely
        request.user.first_name = request.POST.get('first_name', '')
        request.user.last_name = request.POST.get('last_name', '')
        request.user.save()
        
        profile.secondary_email = request.POST.get('secondary_email', '')
        profile.education_level = request.POST.get('education_level', '')
        profile.bio = request.POST.get('bio', '')
        profile.save()
        
        messages.success(request, "Profile updated successfully!")
        return redirect('user_profile')

class UploadView(LoginRequiredMixin, View):
    def post(self, request):
        if 'file' in request.FILES:
            file = request.FILES['file']
            
            # Check for duplicates by title for THIS user
            if AcademicDocument.objects.filter(user=request.user, title=file.name).exists():
                return JsonResponse({
                    'status': 'error', 
                    'message': f'Wait! "{file.name}" has already been uploaded to your library.'
                }, status=400)

            field_of_study = request.POST.get('field_of_study', '').strip() or 'General'
            is_public = request.POST.get('is_public', 'true').lower() in ('true', '1', 'yes', 'on')

            doc = AcademicDocument.objects.create(
                user=request.user,
                title=file.name,
                file=file,
                field_of_study=field_of_study,
                is_public=is_public,
                status='processing'
            )
            return JsonResponse({'status': 'success', 'doc_id': doc.id})
        return JsonResponse({'status': 'error', 'message': 'No file provided'}, status=400)

class ChatView(LoginRequiredMixin, View):
    def get(self, request):
        # Fetch the last 50 messages for this user
        chat_history = ChatMessage.objects.filter(user=request.user).order_by('created_at')[:50]
        history_data = [
            {
                'id': msg.id,
                'query': msg.query,
                'response': msg.response,
                'citations': msg.citations,
                'created_at': msg.created_at.strftime("%b %d, %H:%M")
            } for msg in chat_history
        ]
        return render(request, 'chat.html', {'chat_history': history_data})

    def post(self, request):
        import json
        try:
            data = json.loads(request.body)
            query = data.get('query')
            if not query:
                return JsonResponse({'response': "Please enter a question."}, status=400)
            
            ai_service = AIService()
            
            # 1. Get context (Top-K=3) from user's documents ONLY
            context_text, context_chunks = ai_service.get_context(query, user=request.user, top_k=3)
            
            # 2. Get AI response
            ai_response = ai_service.get_chat_response(query, context_text)
            
            citations = [
                {
                    'title': chunk.document.title,
                    'page': chunk.page_number,
                    'content': chunk.content
                } for chunk in context_chunks
            ]

            # 3. Save to Chat History
            chat_msg = ChatMessage.objects.create(
                user=request.user,
                query=query,
                response=ai_response,
                citations=citations
            )

            return JsonResponse({
                'id': chat_msg.id,
                'response': ai_response,
                'citations': citations
            })
        except Exception as e:
            print(f"Chat error: {e}")
            return JsonResponse({
                'response': f"I encountered an error: {str(e)}. Please make sure your documents are processed and ready.", 
                'citations': []
            }, status=500)

class DeleteChatMessageView(LoginRequiredMixin, View):
    def post(self, request, msg_id):
        msg = get_object_or_404(ChatMessage, id=msg_id, user=request.user)
        msg.delete()
        return JsonResponse({'status': 'success'})

class ClearChatView(LoginRequiredMixin, View):
    def post(self, request):
        ChatMessage.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'success'})


class ToggleVisibilityView(LoginRequiredMixin, View):
    """Let an owner flip a document between public and private."""
    def post(self, request, doc_id):
        doc = get_object_or_404(AcademicDocument, id=doc_id, user=request.user)
        doc.is_public = not doc.is_public
        doc.save(update_fields=['is_public'])
        return JsonResponse({
            'status': 'success',
            'is_public': doc.is_public,
            'message': 'Resource is now public.' if doc.is_public else 'Resource is now private.'
        })

class ReprocessView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        doc = get_object_or_404(AcademicDocument, id=doc_id, user=request.user)
        doc.status = 'processing'
        doc.error_message = None
        doc.save()
        
        # Trigger processing
        from .processor import DocumentProcessor
        import threading
        processor = DocumentProcessor(doc)
        thread = threading.Thread(target=processor.process)
        thread.start()
        
        return JsonResponse({'status': 'success'})

class DeleteDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        doc = get_object_or_404(AcademicDocument, id=doc_id, user=request.user)
        
        # 1. Get all associated chunk IDs
        chunks = DocumentChunk.objects.filter(document=doc)
        chunk_ids = list(chunks.values_list('id', flat=True))
        
        # 2. Remove from Vector Store
        ai_service = AIService()
        ai_service.delete_document_chunks(chunk_ids)
        
        # 3. Delete file from storage
        if doc.file:
            if os.path.exists(doc.file.path):
                os.remove(doc.file.path)
        
        # 4. Delete from Database (chunks are deleted via CASCADE or manually)
        doc.delete()
        
        return JsonResponse({'status': 'success'})

class GlobalResourcesView(LoginRequiredMixin, View):
    def get(self, request):
        query = request.GET.get('q', '')
        field = request.GET.get('field', '')

        docs = AcademicDocument.objects.filter(is_public=True).exclude(user=request.user)
        
        if query:
            docs = docs.filter(Q(title__icontains=query) | Q(field_of_study__icontains=query))
        
        if field:
            docs = docs.filter(field_of_study__iexact=field)

        # Get unique fields of study for the filter dropdown
        fields_of_study = AcademicDocument.objects.filter(is_public=True).values_list('field_of_study', flat=True).distinct()
        fields_of_study = [f for f in fields_of_study if f]

        context = {
            'docs': docs,
            'fields_of_study': sorted(fields_of_study),
            'current_field': field,
            'search_query': query,
        }
        return render(request, 'resources.html', context)

class AcquireDocumentView(LoginRequiredMixin, View):
    def post(self, request, doc_id):
        from django.core.files.base import ContentFile

        # Only public documents can be acquired
        original_doc = get_object_or_404(AcademicDocument, id=doc_id, is_public=True)

        # Prevent acquiring own document
        if original_doc.user == request.user:
            return JsonResponse({
                'status': 'error',
                'message': 'You cannot add your own resource to your library.'
            }, status=400)

        # Check if user already has it
        if AcademicDocument.objects.filter(user=request.user, title=original_doc.title).exists():
            return JsonResponse({
                'status': 'error',
                'message': f'"{original_doc.title}" is already in your library.'
            }, status=400)

        try:
            # Read the original file bytes first — before creating the DB record
            original_path = original_doc.file.path
            with open(original_path, 'rb') as f:
                file_bytes = f.read()
            filename = os.path.basename(original_path)
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': f'Could not read source file: {str(e)}'
            }, status=500)

        # Create doc with status 'pending' so the post_save signal does NOT
        # trigger processing before the file is attached.
        new_doc = AcademicDocument.objects.create(
            user=request.user,
            title=original_doc.title,
            field_of_study=original_doc.field_of_study,
            original_uploader=original_doc.user.username,
            is_public=False,          # acquired copies are private by default
            status='pending'           # signal skips 'pending'
        )

        try:
            # Save file content — this sets new_doc.file but does NOT re-trigger signal
            new_doc.file.save(filename, ContentFile(file_bytes), save=True)

            # Now trigger processing by flipping status to 'processing'
            # The post_save signal only fires for created=True; use a thread directly.
            from .processor import DocumentProcessor
            import threading
            new_doc.status = 'processing'
            new_doc.save(update_fields=['status'])
            processor = DocumentProcessor(new_doc)
            thread = threading.Thread(target=processor.process)
            thread.start()

            return JsonResponse({
                'status': 'success',
                'message': f'✅ "{original_doc.title}" added to your library. AI processing started!'
            })
        except Exception as e:
            new_doc.status = 'error'
            new_doc.error_message = f'Failed to acquire file: {str(e)}'
            new_doc.save()
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
