"""
technical/sync/base.py

Shared look-back window logic for all DataNest sync services.

Bootstrap & look-back rules:
  - No watermark (first ever run)    → pull last 7 days
  - System live < 2 days             → look back 2 days from watermark
  - System live ≥ 2 days (steady)   → look back 1 day from watermark
"""

from datetime import timedelta

from django.utils import timezone

from technical.models import DataSyncLog


def get_sync_window(data_type: str):
    """
    Return (window_start, window_end, last_watermark) for the given data_type.

    window_start accounts for the appropriate look-back so late DSO
    submissions are not missed.
    """
    now = timezone.now()
    last_watermark = DataSyncLog.get_last_watermark(data_type)

    if last_watermark is None:
        # First ever run — bootstrap with 7 days
        window_start = now - timedelta(days=7)
    else:
        # Determine how long the system has been running for this data type
        first_log = (
            DataSyncLog.objects
            .filter(data_type=data_type, status__in=['success', 'partial'])
            .order_by('started_at')
            .first()
        )
        days_live = (now - first_log.started_at).days if first_log else 0

        if days_live < 2:
            # Still in early phase — look back 2 days to be safe
            look_back = timedelta(days=2)
        else:
            # Steady state — 1 day look-back is enough
            look_back = timedelta(days=1)

        window_start = last_watermark - look_back

    return window_start, now, last_watermark
