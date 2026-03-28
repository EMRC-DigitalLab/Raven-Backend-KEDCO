# raven/__init__.py
# This makes Celery auto-discover tasks when Django starts
from .celery import app as celery_app

__all__ = ('celery_app',)
