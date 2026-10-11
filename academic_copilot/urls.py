from django.contrib import admin
from django.urls import path, include

# Uploaded media is intentionally NOT exposed via a public URL; documents are
# served through the permission-checked `document_file` view in core.urls.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]
