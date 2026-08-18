"""Test settings: run the suite without a Postgres server.

Usage::

    python manage.py test --settings=digicrm.settings_test

Everything the calendar work relies on (partial unique constraints, check
constraints, JSONField) is supported by SQLite under Django 4.2, so the suite is
portable.  Postgres remains the production backend.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'NAME': ':memory:'},
    }
}

JWT_SECRET_KEY = 'test-jwt-secret-digicrm-unit-tests'
JWT_ALGORITHM = 'HS256'

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'digicrm-tests',
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Keep the test output readable -- the app logs at DEBUG by default.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'handlers': {'null': {'class': 'logging.NullHandler'}},
    'root': {'handlers': ['null'], 'level': 'CRITICAL'},
}
