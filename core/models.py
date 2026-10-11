import os
import uuid

from django.db import models


def document_upload_path(instance, filename):
    """Store uploads under an unguessable name; the display title keeps the original."""
    ext = os.path.splitext(filename)[1].lower()[:10]
    return f'academic_docs/{uuid.uuid4().hex}{ext}'


class AcademicDocument(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('error', 'Error'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='documents', null=True, blank=True)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=document_upload_path)
    # SHA-256 of the file bytes: lets identical uploads reuse existing embeddings.
    file_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    field_of_study = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. Medicine, Engineering, History")
    is_public = models.BooleanField(default=True, help_text="Allow others to see and acquire this resource")
    original_uploader = models.CharField(max_length=150, blank=True, null=True, help_text="Uploader of the shared resource")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='processing')
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', '-uploaded_at']),
            models.Index(fields=['is_public', 'field_of_study']),
            models.Index(fields=['status', 'updated_at']),
        ]

    def __str__(self):
        return self.title


class DocumentChunk(models.Model):
    document = models.ForeignKey(AcademicDocument, on_delete=models.CASCADE, related_name='chunks')
    content = models.TextField()
    page_number = models.IntegerField(null=True, blank=True)
    chunk_index = models.IntegerField()
    # ID of this chunk's vector in the vector store (ChromaDB).
    vector_id = models.CharField(max_length=255, unique=True, null=True, blank=True)

    class Meta:
        ordering = ['page_number', 'chunk_index']
        indexes = [models.Index(fields=['document', 'page_number', 'chunk_index'])]

    def __str__(self):
        return f"Chunk {self.chunk_index} (Page {self.page_number}) of {self.document.title}"


class UserProfile(models.Model):
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='profile')
    secondary_email = models.EmailField(blank=True, null=True)
    education_level = models.CharField(max_length=100, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Profile for {self.user.username}"


class ChatMessage(models.Model):
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='chats')
    query = models.TextField()
    response = models.TextField()
    citations = models.JSONField(default=list, blank=True)
    # Mean cosine similarity (0–1) of the retrieved sources; null when none were used.
    confidence = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return f"Chat by {self.user.username} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"
