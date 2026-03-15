"""
Test-only settings override.
Forces SQLite for local test runs so we don't need a live PostgreSQL connection
and avoids the 'permission denied to create database' error from Railway credentials.
Also disables Redis in tests (falls back to in-memory cache).
"""
from .settings import *  # noqa: F401, F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',  # noqa: F405
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

# Speed up password hashing in tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]
