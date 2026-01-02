"""
Django settings for digicrm project.
"""

from pathlib import Path
from decouple import config, Csv
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY', default='django-insecure-temporary-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'django_filters',
    'corsheaders',
    'drf_spectacular',
    
    # Local apps - order matters for migrations
    'common',
    'crm',
    'meetings',
    'payments',
    'tasks',
    'integrations',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'common.middleware.JWTAuthenticationMiddleware',  # Add JWT middleware
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'digicrm.urls'

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

WSGI_APPLICATION = 'digicrm.wsgi.application'

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default='postgresql://localhost/digicrm'),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

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

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Additional locations of static files
STATICFILES_DIRS = [
    # Add any custom static directories here if needed
]

# Static files finders
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'common.auth_backends.SuperAdminAuthBackend',
    'common.auth_backends.JWTAuthBackend',
    # Remove ModelBackend since we don't have auth_user table
]

# JWT Settings (must match SuperAdmin)
JWT_SECRET_KEY = config('JWT_SECRET_KEY', default='your-jwt-secret-key-change-in-production')
JWT_ALGORITHM = config('JWT_ALGORITHM', default='HS256')

# SuperAdmin URL
SUPERADMIN_URL = config('SUPERADMIN_URL', default='https://admin.celiyo.com')

# Session settings for admin
SESSION_COOKIE_AGE = 3600 * 8  # 8 hours
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# CORS Settings
CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=False, cast=bool)
CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default='http://localhost:3000,http://localhost:8000',
    cast=Csv()
)


# Allow credentials (cookies, authorization headers, etc.)
CORS_ALLOW_CREDENTIALS = True

# Allow all headers (including custom tenant headers)
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    # Custom tenant headers
    'x-tenant-id',
    'x-tenant-slug',
    'tenanttoken',
]



# Allow common HTTP methods
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]

# Expose headers to the browser
CORS_EXPOSE_HEADERS = [
    'content-type',
    'x-tenant-id',
    'x-tenant-slug',
]

# Cache preflight requests for 1 hour
CORS_PREFLIGHT_MAX_AGE = 3600

# REST Framework Settings
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    # Use custom authentication that works with JWT middleware
    # This prevents SessionAuthentication from interfering with POST requests
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'common.authentication.JWTRequestAuthentication',
    ],
}

# drf-spectacular Settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'DigiCRM API',
    'DESCRIPTION': 'CRM System API Documentation',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api/',
    'COMPONENT_SPLIT_REQUEST': True,
}

# Logging Configuration
import os

# Create logs directory if it doesn't exist
LOGS_DIR = BASE_DIR / 'logs'
os.makedirs(LOGS_DIR, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
        'detailed': {
            'format': '[{asctime}] {levelname} {name} {module}.{funcName}:{lineno} - {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'detailed',
        },
        'file_debug': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'debug.log',
            'maxBytes': 1024*1024*10,  # 10MB
            'backupCount': 5,
            'formatter': 'detailed',
            'level': 'DEBUG',
        },
        'file_info': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'info.log',
            'maxBytes': 1024*1024*10,  # 10MB
            'backupCount': 5,
            'formatter': 'detailed',
            'level': 'INFO',
        },
        'file_error': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'error.log',
            'maxBytes': 1024*1024*10,  # 10MB
            'backupCount': 5,
            'formatter': 'detailed',
            'level': 'ERROR',
        },
        'file_django': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'django.log',
            'maxBytes': 1024*1024*10,  # 10MB
            'backupCount': 5,
            'formatter': 'detailed',
        },
        'file_requests': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'requests.log',
            'maxBytes': 1024*1024*10,  # 10MB
            'backupCount': 5,
            'formatter': 'detailed',
        },
    },
    'root': {
        'handlers': ['console', 'file_debug'],
        'level': 'DEBUG' if DEBUG else 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file_django'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console', 'file_requests', 'file_error'],
            'level': 'DEBUG' if DEBUG else 'WARNING',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console', 'file_debug'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'crm': {
            'handlers': ['console', 'file_info', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'meetings': {
            'handlers': ['console', 'file_info', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'payments': {
            'handlers': ['console', 'file_info', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'tasks': {
            'handlers': ['console', 'file_info', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'common': {
            'handlers': ['console', 'file_debug', 'file_info', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'integrations': {
            'handlers': ['console', 'file_info', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

# ===========================
# INTEGRATIONS CONFIGURATION
# ===========================

# Google OAuth Settings
GOOGLE_CLIENT_ID = config('GOOGLE_CLIENT_ID', default='')
GOOGLE_CLIENT_SECRET = config('GOOGLE_CLIENT_SECRET', default='')
GOOGLE_REDIRECT_URI = config('GOOGLE_REDIRECT_URI', default='http://localhost:8000/api/integrations/connections/oauth_callback/')

# Integration Encryption Key
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
INTEGRATION_ENCRYPTION_KEY = config('INTEGRATION_ENCRYPTION_KEY', default=None)

# ===========================
# CELERY CONFIGURATION
# ===========================

# Celery settings
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes

# Celery Beat Schedule for periodic tasks
CELERY_BEAT_SCHEDULE = {
    'poll-workflow-triggers': {
        'task': 'integrations.tasks.poll_workflow_triggers',
        'schedule': 300.0,  # Every 5 minutes
    },
    'refresh-expiring-tokens': {
        'task': 'integrations.tasks.refresh_expiring_tokens',
        'schedule': 3600.0,  # Every hour
    },
    'cleanup-old-execution-logs': {
        'task': 'integrations.tasks.cleanup_old_execution_logs',
        'schedule': 86400.0,  # Every 24 hours
    },
    'check-connection-health': {
        'task': 'integrations.tasks.check_connection_health',
        'schedule': 86400.0,  # Every 24 hours
    },
}