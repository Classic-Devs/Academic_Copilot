"""
Django settings for academic_copilot project.

All environment-specific values are read from the environment (or a local
.env file). See README.md → "Configuration" for the full list.
"""

from pathlib import Path
import os
import sys

import dj_database_url
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# ─── Core ────────────────────────────────────────────────────────────────────
DEBUG = env.bool('DEBUG', default=False)

# A secret key is mandatory outside DEBUG; local development gets a throwaway one.
SECRET_KEY = env('SECRET_KEY', default='')
if not SECRET_KEY:
    if not DEBUG:
        raise environ.ImproperlyConfigured('SECRET_KEY must be set when DEBUG is False.')
    SECRET_KEY = 'django-insecure-local-development-only-key'

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[
    'localhost',
    '127.0.0.1',
    '.onrender.com',
    '.appspot.com',
    '.run.app',
])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[
    'https://*.onrender.com',
    'https://*.appspot.com',
    'https://*.run.app',
])

GEMINI_API_KEY = env('GEMINI_API_KEY', default='')
GEMINI_CHAT_MODEL = env('GEMINI_CHAT_MODEL', default='gemini-3.1-flash-lite-preview')
GEMINI_EMBEDDING_MODEL = env('GEMINI_EMBEDDING_MODEL', default='gemini-embedding-001')
# gemini-embedding-001 is Matryoshka-trained: 768 dims keep ~all retrieval quality
# at a quarter of the storage and search cost of the 3072-dim default.
EMBEDDING_DIMENSIONS = env.int('EMBEDDING_DIMENSIONS', default=768)

# ─── Applications ────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'academic_copilot.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.static',
            ],
        },
    },
]

WSGI_APPLICATION = 'academic_copilot.wsgi.application'

# ─── Database ────────────────────────────────────────────────────────────────
if os.environ.get('DATABASE_URL'):
    DATABASES = {
        'default': dj_database_url.config(
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=env.bool('DATABASE_SSL_REQUIRE', default=True),
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Cache (rate limiting, query-embedding cache) ────────────────────────────
# Use a shared cache (e.g. redis://...) in multi-instance deployments so rate
# limits apply across processes.
CACHES = {'default': env.cache_url('CACHE_URL', default='locmemcache://academic-copilot')}

# ─── Auth ────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
if 'test' in sys.argv:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'landing'

# ─── Internationalization ────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ─── Static & uploaded files ─────────────────────────────────────────────────
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Uploaded documents are private: they are never served from a public URL,
# only through the permission-checked `document_file` view.
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
if DEBUG or 'test' in sys.argv:
    STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}

# Google Cloud Storage for uploads (required on App Engine / Cloud Run, whose
# local disks are read-only or ephemeral).
GS_BUCKET_NAME = env('GS_BUCKET_NAME', default='')
if GS_BUCKET_NAME:
    STORAGES['default'] = {
        'BACKEND': 'storages.backends.gcloud.GoogleCloudStorage',
        'OPTIONS': {
            'bucket_name': GS_BUCKET_NAME,
            'default_acl': None,       # rely on uniform bucket-level access (private)
            'querystring_auth': True,
            'file_overwrite': False,
        },
    }

# ─── Document processing ─────────────────────────────────────────────────────
MAX_UPLOAD_SIZE_MB = env.int('MAX_UPLOAD_SIZE_MB', default=25)
MAX_CHUNKS_PER_DOCUMENT = env.int('MAX_CHUNKS_PER_DOCUMENT', default=3000)
DOCUMENT_PROCESSING_WORKERS = env.int('DOCUMENT_PROCESSING_WORKERS', default=2)
# A document stuck in "processing" longer than this is treated as interrupted.
PROCESSING_STALE_MINUTES = env.int('PROCESSING_STALE_MINUTES', default=20)

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Vector store: a remote Chroma server (CHROMA_HOST) is recommended in
# production; otherwise a local persistent directory is used.
CHROMA_HOST = env('CHROMA_HOST', default='')
CHROMA_PORT = env.int('CHROMA_PORT', default=8000)
CHROMA_SSL = env.bool('CHROMA_SSL', default=False)
CHROMA_PATH = env('CHROMA_PATH', default=str(BASE_DIR / 'chroma_db'))
CHROMA_COLLECTION = env('CHROMA_COLLECTION', default='academic_chunks')

# Number of reverse proxies in front of the app that append to X-Forwarded-For
# (0 = use REMOTE_ADDR). Render: 1. App Engine / Cloud Run behind Google's LB: 2.
TRUSTED_PROXY_COUNT = env.int('TRUSTED_PROXY_COUNT', default=0)

# ─── Rate limits ("count/seconds") ───────────────────────────────────────────
RATE_LIMITS = {
    'login': env('RATE_LIMIT_LOGIN', default='10/300'),
    'signup': env('RATE_LIMIT_SIGNUP', default='5/3600'),
    'chat': env('RATE_LIMIT_CHAT', default='20/60'),
    'upload': env('RATE_LIMIT_UPLOAD', default='40/3600'),
    'acquire': env('RATE_LIMIT_ACQUIRE', default='30/3600'),
}

# ─── Security hardening ──────────────────────────────────────────────────────
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False

# ─── Logging ─────────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'root': {'handlers': ['console'], 'level': env('LOG_LEVEL', default='INFO')},
    'loggers': {
        'httpx': {'level': 'WARNING'},
        'chromadb': {'level': 'WARNING'},
    },
}
