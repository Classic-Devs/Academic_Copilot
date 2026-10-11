from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db import transaction

from .models import AcademicDocument, ChatMessage, DocumentChunk, UserProfile
from .processor import enqueue_document


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'User Profiles'


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(AcademicDocument)
class AcademicDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'field_of_study', 'is_public', 'status', 'uploaded_at')
    list_filter = ('status', 'is_public', 'uploaded_at')
    search_fields = ('title', 'user__username', 'field_of_study', 'file_hash')
    readonly_fields = ('file_hash', 'uploaded_at', 'updated_at')
    list_select_related = ('user',)
    actions = ('reprocess',)

    @admin.action(description='Re-process selected documents')
    def reprocess(self, request, queryset):
        with transaction.atomic():
            ids = list(queryset.values_list('pk', flat=True))
            queryset.update(status='processing', error_message=None)
            for pk in ids:
                enqueue_document(pk)
        self.message_user(request, f'Queued {len(ids)} document(s) for processing.')


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'page_number', 'chunk_index')
    search_fields = ('content', 'document__title', 'vector_id')
    list_select_related = ('document',)
    raw_id_fields = ('document',)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'confidence')
    list_filter = ('created_at',)
    search_fields = ('user__username', 'query')
    list_select_related = ('user',)
