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
        'notifications.tasks.*':    {'queue': 'notifications'},
        'analytics.tasks.*':        {'queue': 'analytics'},
        'technical.tasks.*':        {'queue': 'datanest_sync'},
        'energy_account.tasks.*':   {'queue': 'datanest_sync'},
        'commercial.tasks.*':       {'queue': 'datanest_sync'},
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
        # DSO meter reading compliance — runs every day at 22:00 Africa/Lagos
        'daily-dso-meter-reading-check': {
            'task': 'notifications.tasks.daily_dso_meter_reading_check',
            'schedule': crontab(hour=22, minute=0),
        },
        # EA monthly submission check — runs on the 10th of each month at 18:00
        'monthly-ea-submission-check': {
            'task': 'notifications.tasks.monthly_ea_submission_check',
            'schedule': crontab(day_of_month=10, hour=18, minute=0),
        },

        # ── Analytics Overview Summary ────────────────────────────────────────────
        # Refresh current month's dashboard summary every day at 01:00 Lagos time
        'daily-overview-summary-refresh': {
            'task': 'analytics.tasks.update_current_month_summary',
            'schedule': crontab(hour=1, minute=0),
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

        # ── DataNest → Raven Energy Account Sync ─────────────────────────────
        # Rates + settings: once per hour (slow-changing)
        'ea-sync-nbet-rates': {
            'task': 'energy_account.tasks.sync_nbet_rates',
            'schedule': crontab(minute=0),
        },
        'ea-sync-settings': {
            'task': 'energy_account.tasks.sync_ea_settings',
            'schedule': crontab(minute=5),
        },
        # Grid meters: every 30 minutes (meter registry changes occasionally)
        'ea-sync-grid-meters': {
            'task': 'energy_account.tasks.sync_grid_meters',
            'schedule': crontab(minute='*/30'),
        },
        # Monthly returns: every 15 minutes (status changes during active period)
        'ea-sync-monthly-returns': {
            'task': 'energy_account.tasks.sync_monthly_returns',
            'schedule': crontab(minute='*/15'),
        },
        # Monthly readings: every 15 minutes
        'ea-sync-monthly-readings': {
            'task': 'energy_account.tasks.sync_monthly_readings',
            'schedule': crontab(minute='*/15'),
        },
        # Feeder technical energy: every 30 minutes
        'ea-sync-feeder-technical-energy': {
            'task': 'energy_account.tasks.sync_feeder_technical_energy',
            'schedule': crontab(minute='*/30'),
        },
        # TCN reconciliation: every 15 minutes (active during reconciliation window)
        'ea-sync-tcn-reconciliation': {
            'task': 'energy_account.tasks.sync_tcn_reconciliation',
            'schedule': crontab(minute='*/15'),
        },
        'ea-sync-tcn-reconciliation-notes': {
            'task': 'energy_account.tasks.sync_tcn_reconciliation_notes',
            'schedule': crontab(minute='*/15'),
        },
        # MO reconciliation: every 30 minutes
        'ea-sync-mo-reconciliation': {
            'task': 'energy_account.tasks.sync_mo_reconciliation',
            'schedule': crontab(minute='*/30'),
        },
        # Weekly readings: every 30 minutes
        'ea-sync-weekly-readings': {
            'task': 'energy_account.tasks.sync_weekly_readings',
            'schedule': crontab(minute='*/30'),
        },
        # Operational tables: once per hour
        'ea-sync-station-assignments': {
            'task': 'energy_account.tasks.sync_station_assignments',
            'schedule': crontab(minute=10),
        },
        'ea-sync-meter-check-schedules': {
            'task': 'energy_account.tasks.sync_meter_check_schedules',
            'schedule': crontab(minute=15),
        },
        'ea-sync-meter-check-records': {
            'task': 'energy_account.tasks.sync_meter_check_records',
            'schedule': crontab(minute=20),
        },
        'ea-sync-coupling-log': {
            'task': 'energy_account.tasks.sync_coupling_log',
            'schedule': crontab(minute=25),
        },

        # ── DataNest -> Raven Commercial Sync ─────────────────────────────────
        # Meter readings: every 5 minutes (field officers submit throughout the week)
        'commercial-sync-readings': {
            'task': 'commercial.tasks.sync_commercial_readings_task',
            'schedule': crontab(minute='*/5'),
        },
        # Customers: every hour (new enrollments, meter status changes)
        'commercial-sync-customers': {
            'task': 'commercial.tasks.sync_commercial_customers_task',
            'schedule': crontab(minute=35),
        },
        # Managers + assignments: every hour
        'commercial-sync-managers': {
            'task': 'commercial.tasks.sync_commercial_managers_task',
            'schedule': crontab(minute=40),
        },
        # Tariff rates: every hour (rarely changes)
        'commercial-sync-tariff-rates': {
            'task': 'commercial.tasks.sync_commercial_tariff_task',
            'schedule': crontab(minute=45),
        },

        # ── Google Sheet Live Feeds ───────────────────────────────────────────
        # 33KV load-flow: every hour at minute 30
        'sync-33kv-google-sheet': {
            'task': 'tmo.tasks.sync_33kv_sheet_task',
            'schedule': crontab(minute=30),
        },
        # 11KV load-flow: every hour at minute 45
        'sync-11kv-google-sheet': {
            'task': 'tmo.tasks.sync_11kv_sheet_task',
            'schedule': crontab(minute=45),
        },
        # 33KV energy accounting: every hour at minute 50
        'sync-33kv-energy-sheet': {
            'task': 'tmo.tasks.sync_33kv_energy_sheet_task',
            'schedule': crontab(minute=50),
        },
        # End-of-month reminder: 25th at 08:00 Lagos time
        # Alerts ops team to register next month's Google Sheet links
        'monthly-sheet-feed-reminder': {
            'task': 'tmo.tasks.send_monthly_sheet_reminder_task',
            'schedule': crontab(day_of_month=25, hour=8, minute=0),
        },

        # ── DataNest -> Raven TMO Sync ────────────────────────────────────────
        # TMO data changes infrequently (monthly targets) — sync every 30 min
        'tmo-sync-feeder-targets': {
            'task': 'commercial.tasks.sync_tmo_feeder_targets_task',
            'schedule': crontab(minute='*/30'),
        },
        'tmo-sync-collection-targets': {
            'task': 'commercial.tasks.sync_tmo_collection_targets_task',
            'schedule': crontab(minute='*/30'),
        },
        'tmo-sync-billing-efficiency': {
            'task': 'commercial.tasks.sync_tmo_billing_efficiency_task',
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
