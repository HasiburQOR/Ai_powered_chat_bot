import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-default-key-change-me')

DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 't')

ALLOWED_HOSTS = [h.strip() for h in os.environ.get('ALLOWED_HOSTS', '*').split(',') if h.strip()]

# Needed when the site is served over HTTPS behind a reverse proxy (Caddy/nginx/Cloudflare).
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()
]

# ---- Auth redirects (branded login lives at /accounts/login/) ----
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'post-login-redirect'
LOGOUT_REDIRECT_URL = 'login'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party apps
    'django_htmx',
    'pgvector',

    # Project domain apps
    'platforms',
    'llm',
    'knowledge',
    'conversations',
    'profiles',
    'bot',
    'webhooks',
    'widget',
    'dashboard',
    'accounts',
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
    'django_htmx.middleware.HtmxMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

POSTGRES_DB = os.environ.get('POSTGRES_DB', 'chatbot')
POSTGRES_USER = os.environ.get('POSTGRES_USER', 'chatbot')
POSTGRES_PASSWORD = os.environ.get('POSTGRES_PASSWORD', 'chatbot_secret_pass')
POSTGRES_HOST = os.environ.get('POSTGRES_HOST', 'db')
POSTGRES_PORT = os.environ.get('POSTGRES_PORT', '5432')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': POSTGRES_DB,
        'USER': POSTGRES_USER,
        'PASSWORD': POSTGRES_PASSWORD,
        'HOST': POSTGRES_HOST,
        'PORT': POSTGRES_PORT,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# WhiteNoise serves collected static files straight from Gunicorn (admin CSS/JS etc.)
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Celery Configuration
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# Field Encryption
FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY', 'V3pLV1RvbFFuSmZ5WE95SGpldWVTVW15QWNjSlk3b0M=')

# ---- Email (used by password reset / password change notifications) ----
# Dev default: emails are printed to the console. Production: set SMTP vars in .env.
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 't', 'yes')
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 't', 'yes')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Chatbot Studio <noreply@example.com>')

# Password reset links expire after 24 hours.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24

# ---- Production security hardening ----
# Applied when DEBUG=False (i.e. live). Widget iframe views are individually exempt
# and use a scoped Content-Security-Policy, so a global X-Frame-Options DENY is safe.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 't', 'yes')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = 'same-origin'

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# ---- Logging ----
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '[{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
    },
}
