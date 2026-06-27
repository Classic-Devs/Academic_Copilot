from django.db import models

class AcademicDocument(models.Model):
    STATUS_CHOICES = [
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('error', 'Error'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='documents', null=True, blank=True)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='academic_docs/')
    field_of_study = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. Medicine, Engineering, History")
    is_public = models.BooleanField(default=True, help_text="Allow others to see and acquire this resource")
    original_uploader = models.CharField(max_length=150, blank=True, null=True, help_text="Uploader of the shared resource")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='processing')
    error_message = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.title

class DocumentChunk(models.Model):
    document = models.ForeignKey(AcademicDocument, on_delete=models.CASCADE, related_name='chunks')
    content = models.TextField()
    page_number = models.IntegerField(null=True, blank=True)
    chunk_index = models.IntegerField()
    # vector_id for vector DBs (e.g. ChromaDB), vector_data for local JSON storage
    vector_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    vector_data = models.JSONField(null=True, blank=True) 

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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Chat by {self.user.username} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"
