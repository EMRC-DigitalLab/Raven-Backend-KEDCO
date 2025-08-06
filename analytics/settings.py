# analytics/settings.py
"""
Analytics app settings configuration
Add these to your main Django settings.py file
"""

from django.conf import settings

# Analytics signals configuration
ANALYTICS_SIGNALS_ENABLED = getattr(settings, 'ANALYTICS_SIGNALS_ENABLED', True)

# Celery configuration for analytics tasks
ANALYTICS_USE_CELERY = getattr(settings, 'ANALYTICS_USE_CELERY', True)

# Task delay settings (in seconds)
ANALYTICS_TASK_DELAY = getattr(settings, 'ANALYTICS_TASK_DELAY', 5)

# Enable/disable specific signal types
ANALYTICS_COMMERCIAL_SIGNALS = getattr(settings, 'ANALYTICS_COMMERCIAL_SIGNALS', True)
ANALYTICS_TECHNICAL_SIGNALS = getattr(settings, 'ANALYTICS_TECHNICAL_SIGNALS', True)
ANALYTICS_FINANCIAL_SIGNALS = getattr(settings, 'ANALYTICS_FINANCIAL_SIGNALS', True)
ANALYTICS_HR_SIGNALS = getattr(settings, 'ANALYTICS_HR_SIGNALS', True)

# Logging configuration
ANALYTICS_LOG_LEVEL = getattr(settings, 'ANALYTICS_LOG_LEVEL', 'INFO')

# Cache timeout for signal disable flag (in seconds)
ANALYTICS_SIGNALS_CACHE_TIMEOUT = getattr(settings, 'ANALYTICS_SIGNALS_CACHE_TIMEOUT', 3600)


def get_analytics_settings():
    """Get all analytics settings as a dictionary"""
    return {
        'signals_enabled': ANALYTICS_SIGNALS_ENABLED,
        'use_celery': ANALYTICS_USE_CELERY,
        'task_delay': ANALYTICS_TASK_DELAY,
        'commercial_signals': ANALYTICS_COMMERCIAL_SIGNALS,
        'technical_signals': ANALYTICS_TECHNICAL_SIGNALS,
        'financial_signals': ANALYTICS_FINANCIAL_SIGNALS,
        'hr_signals': ANALYTICS_HR_SIGNALS,
        'log_level': ANALYTICS_LOG_LEVEL,
        'cache_timeout': ANALYTICS_SIGNALS_CACHE_TIMEOUT,
    }


# Example Django settings.py configuration:
"""
# Add to your settings.py file:

# Analytics Configuration
ANALYTICS_SIGNALS_ENABLED = True  # Set to False to disable all analytics signals
ANALYTICS_USE_CELERY = False  # Set to True when Celery is properly configured
ANALYTICS_TASK_DELAY = 5  # Delay in seconds before executing summary update tasks

# Celery Configuration (when ANALYTICS_USE_CELERY = True)
CELERY_BROKER_URL = 'redis://localhost:6379/0'  # or your broker URL
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

# Optional: Route analytics tasks to specific queue
CELERY_TASK_ROUTES = {
    'analytics.signals.*': {'queue': 'analytics'},
}

# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'analytics_file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'logs/analytics.log',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'analytics.signals': {
            'handlers': ['analytics_file', 'console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}
"""