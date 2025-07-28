# analytics/apps.py
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'analytics'
    verbose_name = 'Analytics'
    
    # def ready(self):
    #     """
    #     Import signal handlers when Django starts.
    #     This ensures that our signals are connected and will trigger
    #     summary updates when source data changes.
    #     """
    #     try:
    #         # Import signals to register them
    #         import analytics.signals
    #     except ImportError:
    #         # Handle case where signals module doesn't exist yet
    #         pass