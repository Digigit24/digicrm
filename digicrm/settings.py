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
    'real_estate',
    'meetings',
    'payments',
    'tasks',
    'notifications',
    'scheduling',            # Calendar / scheduling (URL prefix /api/calendar/)
    'integrations',
    'telephony',
    'whatsapp_integration',   # DigiCRM WhatsApp adapter app
    'ai',                     # AI copilot chat endpoint (Phase 1)
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

# How long (seconds) the tenant user directory fetched from SuperAdmin is cached
# server-side. Cache keys are always tenant-scoped (crm/user_directory.py).
USER_DIRECTORY_CACHE_TTL = config('USER_DIRECTORY_CACHE_TTL', default=300, cast=int)

# Session settings for admin
SESSION_COOKIE_AGE = 3600 * 8  # 8 hours
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# CORS Settings
CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=False, cast=bool)
_cors_origins = config(
    'CORS_ALLOWED_ORIGINS',
    default='http://localhost:3000,http://localhost:8000',
    cast=Csv()
)
# Ensure all origins have a scheme (add https:// if missing)
CORS_ALLOWED_ORIGINS = [
    origin if origin.startswith(('http://', 'https://')) else f'https://{origin}'
    for origin in _cors_origins
]


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
    # Zata storage headers
    'x-zata-bucket',
    'x-zata-folder-id',
    # WhatsApp vendor credentials (passed from frontend localStorage)
    'x-wa-vendor-uid',
    'x-wa-api-token',
    'x-wa-base-url',
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
    'DEFAULT_PAGINATION_CLASS': 'common.pagination.StandardPagination',
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
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # Scoped throttles used by the Composio endpoints (integrations/views_composio.py).
    # Only views that declare `throttle_scope` are affected; nothing else throttles.
    'DEFAULT_THROTTLE_CLASSES': [],
    'DEFAULT_THROTTLE_RATES': {
        'composio-initiate': '10/min',
        'composio-status': '30/min',
        'composio-execute': '60/min',
    },
}

# drf-spectacular Settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'DigiCRM API',
    'DESCRIPTION': 'CRM System API Documentation',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api/',
    'COMPONENT_SPLIT_REQUEST': True,
    'ENUM_NAME_OVERRIDES': {
        'LeadPriorityEnum': 'crm.models.PriorityEnum.choices',
        'LeadActivityTypeEnum': 'crm.models.ActivityTypeEnum.choices',
        'TaskStatusEnum': 'tasks.models.TaskStatusEnum.choices',
        'PaymentTypeEnum': 'payments.models.PaymentTypeEnum.choices',
        'PaymentStatusEnum': 'payments.models.PaymentStatusEnum.choices',
        'IntegrationTypeEnum': 'integrations.models.IntegrationTypeEnum.choices',
        'ConnectionStatusEnum': 'integrations.models.ConnectionStatusEnum.choices',
        'WorkflowTriggerTypeEnum': 'integrations.models.TriggerTypeEnum.choices',
        'WorkflowActionTypeEnum': 'integrations.models.ActionTypeEnum.choices',
        'WorkflowExecutionStatusEnum': 'integrations.models.ExecutionStatusEnum.choices',
    },
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
        'file_mcp': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'mcp.log',
            'maxBytes': 1024*1024*10,  # 10MB
            'backupCount': 5,
            'formatter': 'detailed',
            'level': 'DEBUG',
        },
    },
    'root': {
        'handlers': ['console', 'file_debug'],
        'level': 'DEBUG' if DEBUG else 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file_django'],
            'level': 'WARNING',  # Temporarily reduced to see actual errors
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console', 'file_requests', 'file_error'],
            'level': 'DEBUG' if DEBUG else 'WARNING',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console', 'file_debug'],
            'level': 'WARNING',  # Temporarily reduced to see actual errors
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
        'whatsapp_integration': {
            'handlers': ['console', 'file_debug', 'file_info', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
        # MCP server — all endpoints: oauth, sse, message, dispatch
        'mcp': {
            'handlers': ['console', 'file_mcp', 'file_error'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

# ===========================
# ZATA STORAGE CONFIGURATION
# ===========================
ZATA_API_URL = config('ZATA_API_URL', default='')
ZATA_API_TOKEN = config('ZATA_API_TOKEN', default='')

# ===========================
# INTEGRATIONS CONFIGURATION
# ===========================

# Google OAuth Settings
GOOGLE_CLIENT_ID = config('GOOGLE_CLIENT_ID', default='')
GOOGLE_CLIENT_SECRET = config('GOOGLE_CLIENT_SECRET', default='')
GOOGLE_REDIRECT_URI = config('GOOGLE_REDIRECT_URI', default='http://localhost:8000/api/integrations/connections/oauth_callback/')

# Frontend URL for OAuth redirects
FRONTEND_URL = config('FRONTEND_URL', default='http://localhost:3000')
FRONTEND_OAUTH_CALLBACK_URL = config('FRONTEND_OAUTH_CALLBACK_URL', default=f"{config('FRONTEND_URL', default='http://localhost:3000')}/integrations/oauth/callback")

# Integration Encryption Key
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
INTEGRATION_ENCRYPTION_KEY = config('INTEGRATION_ENCRYPTION_KEY', default=None)

# TeleCMI master key (KEK) — wraps each tenant's own data key.
#
# TeleCMI credentials use envelope encryption (telephony/services/crypto.py):
# every tenant gets its own Fernet key, generated server-side and stored on the
# credential row in encrypted form. THIS value is the single key that wraps all
# of them. It is one constant per database, not one per tenant.
#
# Every deployment that talks to the same database MUST set the same value.
# Leaving it unset falls back to SECRET_KEY, which is what caused secrets saved
# on one environment to be unreadable on another.
#
# Generate once with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TELECMI_MASTER_KEY = config('TELECMI_MASTER_KEY', default=None)

# ===========================
# COMPOSIO (managed third-party tool auth)
# ===========================
# Composio brokers OAuth for hundreds of third-party toolkits (Notion, Gmail,
# Google Drive, Google Calendar, ...). It COEXISTS with the native Google OAuth
# above; it does not replace it. See _plans/05-composio-integration.md.
#
# COMPOSIO_API_KEY is server-side ONLY. It grants access to EVERY connected
# account across EVERY tenant, so it must never be sent to a browser, logged,
# returned by an API response, or persisted on a model. It is read in exactly
# one place: integrations/services/composio_client.py.
COMPOSIO_API_KEY = config('COMPOSIO_API_KEY', default='')
COMPOSIO_BASE_URL = config('COMPOSIO_BASE_URL', default='https://backend.composio.dev/api/v3.1')
COMPOSIO_ENABLED = config('COMPOSIO_ENABLED', default=False, cast=bool)

# Namespace prefix for Composio entity ids: "{namespace}:{tenant_id}:{user_id}".
# Composio's user_id is the ONLY isolation boundary it enforces between end
# users, so it encodes both the tenant and the environment. This value MUST
# differ per environment (celiyo-dev / celiyo-staging / celiyo-prod) so a
# staging box pointed at the same Composio project can never address a
# production tenant's connected accounts.
COMPOSIO_USER_NAMESPACE = config('COMPOSIO_USER_NAMESPACE', default='celiyo-dev')

# Where Composio sends the browser after hosted auth completes. Public path —
# also listed in common.middleware.JWTAuthenticationMiddleware.PUBLIC_PATHS.
COMPOSIO_CALLBACK_URL = config(
    'COMPOSIO_CALLBACK_URL',
    default='http://localhost:8000/api/integrations/composio/callback/',
)
# Frontend page the callback bounces the browser to (query params appended).
COMPOSIO_FRONTEND_RETURN_URL = config(
    'COMPOSIO_FRONTEND_RETURN_URL',
    default=f"{config('FRONTEND_URL', default='http://localhost:3000')}/integrations/composio/callback",
)
# Open-redirect guard: allowlist of RELATIVE paths a caller may ask to return to.
COMPOSIO_RETURN_TO_ALLOWLIST = ['/integrations', '/settings/integrations']

# Shared secret from composio.triggers.set_webhook_subscription()['secret'].
# Blank => every inbound Composio webhook is rejected (safe default).
COMPOSIO_WEBHOOK_SECRET = config('COMPOSIO_WEBHOOK_SECRET', default='')
# Reject webhooks whose webhook-timestamp header is older/newer than this many
# seconds (replay window).
COMPOSIO_WEBHOOK_TOLERANCE_SECONDS = config('COMPOSIO_WEBHOOK_TOLERANCE_SECONDS', default=300, cast=int)

COMPOSIO_HTTP_TIMEOUT = config('COMPOSIO_HTTP_TIMEOUT', default=20, cast=int)
COMPOSIO_MAX_RETRIES = config('COMPOSIO_MAX_RETRIES', default=3, cast=int)
# How long a hosted-auth link / ComposioLinkState nonce stays valid.
COMPOSIO_LINK_TTL_SECONDS = config('COMPOSIO_LINK_TTL_SECONDS', default=900, cast=int)
# Minimum seconds between two outbound status polls for the same connection.
COMPOSIO_STATUS_MIN_INTERVAL = config('COMPOSIO_STATUS_MIN_INTERVAL', default=10, cast=int)
# POST /connections/{id}/execute/ ships DARK. Never enable without an explicit
# ComposioAuthConfig.restrict_to_tools allowlist on the auth config.
COMPOSIO_EXECUTE_ENABLED = config('COMPOSIO_EXECUTE_ENABLED', default=False, cast=bool)
# Toolkits offered out of the box (used by bootstrap_composio_auth_configs).
COMPOSIO_PRIORITY_TOOLKITS = ['GMAIL', 'NOTION', 'GOOGLEDRIVE', 'GOOGLECALENDAR']

# ===========================
# PUSHER (real-time telephony live events)
# ===========================
# Server-side credentials for PUBLISHING events from CDRWebhookView /
# LiveEventWebhookView (telephony/services/realtime.py). Must be the SAME
# Pusher app the frontend subscribes to — sepratecrm's
# src/hooks/useTelephonyLiveEvents.ts and src/services/pusherService.ts
# already hardcode PUSHER_KEY='649db422ae8f2e9c7a9d' / cluster='ap2' for that
# existing app, so those two default below match it. Only PUSHER_APP_ID and
# PUSHER_SECRET (both private, server-only) need to come from your real
# Pusher dashboard — get them from the same Pusher account/app that issued
# that key. Leave PUSHER_SECRET blank to no-op (live events silently won't
# publish; nothing else breaks).
PUSHER_APP_ID = config('PUSHER_APP_ID', default='')
PUSHER_KEY = config('PUSHER_KEY', default='649db422ae8f2e9c7a9d')
PUSHER_SECRET = config('PUSHER_SECRET', default='')
PUSHER_CLUSTER = config('PUSHER_CLUSTER', default='ap2')

# ===========================
# CACHE CONFIGURATION
# ===========================
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': str(BASE_DIR / '.cache'),
    }
}

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
    # ── Composio ──
    'sync-composio-toolkits': {
        'task': 'integrations.tasks.sync_composio_toolkits',
        'schedule': 86400.0,  # Every 24 hours
    },
    'sweep-stale-composio-connections': {
        'task': 'integrations.tasks.sweep_stale_composio_connections',
        'schedule': 900.0,  # Every 15 minutes
    },
    'cleanup-composio-events': {
        'task': 'integrations.tasks.cleanup_composio_events',
        'schedule': 86400.0,  # Every 24 hours
    },
    'sync-telecmi-cdrs': {
        'task': 'telephony.tasks.sync_all_telecmi_cdrs',
        'schedule': 300.0,  # Every 5 minutes
        'kwargs': {'hours_back': 1},
    },
    'dispatch-due-reminders': {
        'task': 'notifications.tasks.dispatch_due_reminders',
        'schedule': 30.0,
    },
    'materialize-meeting-reminders': {
        'task': 'meetings.tasks.materialize_meeting_reminders',
        'schedule': 900.0,  # Every 15 minutes
    },
}

# Due reminders older than this are marked missed instead of surfacing as stale alerts.
REMINDER_DELIVERY_GRACE_HOURS = config('REMINDER_DELIVERY_GRACE_HOURS', default=24, cast=int)
REMINDER_MAX_ATTEMPTS = config('REMINDER_MAX_ATTEMPTS', default=5, cast=int)
