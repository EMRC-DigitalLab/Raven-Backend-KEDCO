# analytics/apps.py
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'analytics'
    verbose_name = 'Analytics & Reporting'
    
    def ready(self):
        """
        Import signal handlers when Django starts.
        This ensures that our signals are connected and will trigger
        summary updates when source data changes.
        """
        import analytics.signals  # This registers all the signal handlers
        
        # Optionally, you can add startup checks here
        self.perform_startup_checks()
    
    def perform_startup_checks(self):
        """
        Perform basic health checks on startup.
        This can help identify configuration issues early.
        """
        try:
            from django.core.cache import cache
            from django.conf import settings
            import logging
            
            logger = logging.getLogger(__name__)
            
            # Test cache connectivity
            cache_test_key = 'analytics_startup_test'
            cache.set(cache_test_key, 'test_value', 60)
            if cache.get(cache_test_key) == 'test_value':
                logger.info("✅ Analytics app: Cache connectivity verified")
                cache.delete(cache_test_key)
            else:
                logger.warning("⚠️ Analytics app: Cache connectivity issue detected")
            
            # Test Celery connectivity (if available)
            try:
                from celery import current_app
                if current_app.control.inspect().active() is not None:
                    logger.info("✅ Analytics app: Celery connectivity verified")
                else:
                    logger.warning("⚠️ Analytics app: Celery workers not detected")
            except ImportError:
                logger.info("ℹ️ Analytics app: Celery not installed (signals will use threading)")
            except Exception as e:
                logger.warning(f"⚠️ Analytics app: Celery connectivity issue: {str(e)}")
            
            # Check if summary model table exists (for migrations)
            try:
                from .models import MonthlyOverviewSummary
                MonthlyOverviewSummary.objects.exists()
                logger.info("✅ Analytics app: Database connectivity verified")
            except Exception as e:
                logger.warning(f"⚠️ Analytics app: Database issue (probably migrations needed): {str(e)}")
                
        except Exception as e:
            # Don't fail startup due to health check issues
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"❌ Analytics app startup checks failed: {str(e)}")


# analytics/__init__.py
default_app_config = 'analytics.apps.AnalyticsConfig'