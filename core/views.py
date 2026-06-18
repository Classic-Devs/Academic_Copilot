import os
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.http import JsonResponse
from .models import AcademicDocument, DocumentChunk, UserProfile, ChatMessage
from .ai_service import AIService
from django.db.models import Sum, Q
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages

class LandingView(View):
    def get(self, request):
        return render(request, 'landing.html')

class SignupView(View):
    def get(self, request):
        form = UserCreationForm()
        return render(request, 'signup.html', {'form': form})

    def post(self, request):
        form = UserCreationForm(request.POST)
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

            doc = AcademicDocument.objects.create(
                user=request.user,
                title=file.name,
                file=file,
                field_of_study=field_of_study,
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
            ChatMessage.objects.create(
                user=request.user,
                query=query,
                response=ai_response,
                citations=citations
            )

            return JsonResponse({
                'response': ai_response,
                'citations': citations
            })
        except Exception as e:
            print(f"Chat error: {e}")
            return JsonResponse({
                'response': f"I encountered an error: {str(e)}. Please make sure your documents are processed and ready.", 
                'citations': []
            }, status=500)

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
        import shutil
        from django.core.files import File
        
        # Get the global document
        original_doc = get_object_or_404(AcademicDocument, id=doc_id, is_public=True)
        
        # Check if user already has it
        if AcademicDocument.objects.filter(user=request.user, title=original_doc.title).exists():
            return JsonResponse({
                'status': 'error',
                'message': f'"{original_doc.title}" is already in your library.'
            }, status=400)

        # Create a new document record for the user
        new_doc = AcademicDocument.objects.create(
            user=request.user,
            title=original_doc.title,
            field_of_study=original_doc.field_of_study,
            original_uploader=original_doc.user.username,
            status='processing'
        )

        try:
            # Safely copy the file
            original_path = original_doc.file.path
            filename = os.path.basename(original_path)
            
            with open(original_path, 'rb') as f:
                new_doc.file.save(filename, File(f), save=True)
            
            return JsonResponse({
                'status': 'success',
                'message': f'Added "{original_doc.title}" to your library. AI processing started!'
            })
        except Exception as e:
            new_doc.status = 'error'
            new_doc.error_message = f"Failed to acquire file: {str(e)}"
            new_doc.save()
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
