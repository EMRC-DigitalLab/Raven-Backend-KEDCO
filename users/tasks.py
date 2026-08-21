# users/tasks.py
import logging

from celery import shared_task
from django.core.management import call_command
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def purge_expired_sessions():
    """Mark UserSession rows inactive once their underlying token has expired."""
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

    from .models import UserSession

    expired_jtis = OutstandingToken.objects.filter(expires_at__lt=timezone.now()).values_list('jti', flat=True)
    updated = UserSession.objects.filter(jti__in=list(expired_jtis), is_active=True).update(
        is_active=False, revoked_at=timezone.now()
    )
    logger.info("purge_expired_sessions: marked %s session(s) inactive", updated)
    return updated


@shared_task
def flush_expired_blacklist_tokens():
    """Wrapper around simplejwt's flushexpiredtokens management command."""
    call_command('flushexpiredtokens')
    logger.info("flush_expired_blacklist_tokens: ran flushexpiredtokens")
