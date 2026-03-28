# raven/celery.py
import os

from celery import Celery
from decouple import config

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')

app = Celery('raven')

# Use Redis as the broker and result backend
REDIS_URL = config('REDIS_URL', default='redis://127.0.0.1:6379/2')

app.conf.update(
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    accept_content=['json'],
    task_serializer='json',
    result_serializer='json',
    timezone='Africa/Lagos',
    enable_utc=True,
    # Task routing
    task_routes={
        'notifications.tasks.*': {'queue': 'notifications'},
        'analytics.tasks.*': {'queue': 'analytics'},
    },
    # Retry settings
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Auto-discover tasks in all installed apps
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
