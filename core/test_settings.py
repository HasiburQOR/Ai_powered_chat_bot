"""SQLite-based settings for running the test suite without Postgres/pgvector.

Usage:
    python manage.py test --settings=core.test_settings

The knowledge app relies on Postgres-only pgvector columns and raw SQL
migrations (CREATE EXTENSION vector), so its migrations are skipped entirely
here. Knowledge lookups at runtime are failure-tolerant (the widget's send
endpoint wraps the bot engine in try/except), so tests still exercise the
full request cycle without a Postgres server.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Skip pgvector-dependent migrations for the knowledge app in tests.
MIGRATION_MODULES = {
    "knowledge": None,
}

# Faster (and sufficient for the test suite).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
