# raven/celery.py
import os

from celery import Celery
from celery.schedules import crontab
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
        'technical.tasks.*': {'queue': 'datanest_sync'},
    },
    # Retry settings
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # ── Celery Beat scheduled tasks ───────────────────────────────────────────
    beat_schedule={
        # Band A SBT check — runs every day at 23:30 Africa/Lagos
        'daily-band-a-supply-check': {
            'task': 'notifications.tasks.daily_band_a_supply_check',
            'schedule': crontab(hour=23, minute=30),
        },
        # Pending restoration check — runs every day at 23:30 Africa/Lagos
        'daily-restoration-check': {
            'task': 'notifications.tasks.daily_restoration_check',
            'schedule': crontab(hour=23, minute=30),
        },

        # ── DataNest → Raven Technical Sync ───────────────────────────────────
        # Hourly load: every 5 minutes
        'datanest-sync-hourly-load': {
            'task': 'technical.tasks.sync_hourly_load_task',
            'schedule': crontab(minute='*/5'),
        },
        # Interruptions/faults: every 5 minutes
        'datanest-sync-interruptions': {
            'task': 'technical.tasks.sync_interruptions_task',
            'schedule': crontab(minute='*/5'),
        },
        # Cumulative meter readings: every 30 minutes
        'datanest-sync-meter-readings': {
            'task': 'technical.tasks.sync_meter_readings_task',
            'schedule': crontab(minute='*/30'),
        },
    },
)

# Auto-discover tasks in all installed apps
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
