"""
technical/views/sync_backfill.py

Two endpoints that let the frontend trigger and monitor a historical data
backfill without touching the server terminal.

POST /api/technical/sync/backfill/
    Trigger a backfill.  Admin / super_admin only.
    Body (JSON):
        {
            "start_date": "2026-04-01",   // required
            "end_date":   "2026-04-30",   // required
            "hourly":  true,              // optional, default true
            "energy":  true               // optional, default true
        }
    Response 202:
        {
            "job_id":     "<celery-task-uuid>",
            "status_url": "/api/technical/sync/backfill/<job_id>/",
            "message":    "Backfill queued for 2026-04-01 → 2026-04-30"
        }

GET /api/technical/sync/backfill/<job_id>/
    Poll job progress.  Any authenticated user.
    Response 200:
        {
            "job_id":  "<uuid>",
            "state":   "PENDING" | "PROGRESS" | "SUCCESS" | "FAILURE",
            "pct":     75,
            "current_range": "2026-04-22 → 2026-04-28",
            "totals":  { hourly: {...}, energy: {...} },
            "result":  { ... }   // only when state == SUCCESS
        }
"""

import datetime

from celery.result import AsyncResult
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status


# ── permission helper ──────────────────────────────────────────────────────────

def _is_admin(user):
    return getattr(user, 'role', None) in ('super_admin', 'admin')


# ── views ──────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_backfill(request):
    """
    Trigger a historical data backfill from DataNest into Raven.
    Only admin / super_admin users may call this.
    """
    if not _is_admin(request.user):
        return Response(
            {'error': 'Only admin or super_admin users can trigger a backfill.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    data = request.data

    # ── Validate dates ────────────────────────────────────────────────────────
    start_str = data.get('start_date', '')
    end_str   = data.get('end_date', '')

    try:
        start_date = datetime.date.fromisoformat(start_str)
    except (ValueError, TypeError):
        return Response(
            {'error': 'start_date is required and must be YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        end_date = datetime.date.fromisoformat(end_str)
    except (ValueError, TypeError):
        return Response(
            {'error': 'end_date is required and must be YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if end_date < start_date:
        return Response(
            {'error': 'end_date must be on or after start_date.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    max_range = datetime.timedelta(days=366)
    if (end_date - start_date) > max_range:
        return Response(
            {'error': 'Date range cannot exceed 366 days per request.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    do_hourly = bool(data.get('hourly', True))
    do_energy = bool(data.get('energy', True))

    if not do_hourly and not do_energy:
        return Response(
            {'error': 'At least one of hourly or energy must be true.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Queue the Celery task ─────────────────────────────────────────────────
    from technical.tasks import backfill_technical_data_task

    task = backfill_technical_data_task.apply_async(
        kwargs={
            'start_date_str': str(start_date),
            'end_date_str':   str(end_date),
            'do_hourly':      do_hourly,
            'do_energy':      do_energy,
        },
        queue='datanest_sync',
    )

    status_url = f'/api/technical/sync/backfill/{task.id}/'

    return Response(
        {
            'job_id':     task.id,
            'status_url': status_url,
            'message':    (
                f'Backfill queued for {start_date} → {end_date} '
                f'(hourly={do_hourly}, energy={do_energy}). '
                f'Poll status_url for progress.'
            ),
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def backfill_status(request, job_id):
    """
    Poll the status of a previously queued backfill job.
    """
    result = AsyncResult(job_id)
    state  = result.state    # PENDING | STARTED | PROGRESS | SUCCESS | FAILURE | REVOKED

    payload = {
        'job_id': job_id,
        'state':  state,
        'pct':    0,
        'current_range': None,
        'totals': None,
        'result': None,
        'error':  None,
    }

    if state == 'PROGRESS':
        meta = result.info or {}
        payload.update({
            'pct':           meta.get('pct', 0),
            'current_range': meta.get('current_range'),
            'totals':        meta.get('totals'),
        })

    elif state == 'SUCCESS':
        res = result.result or {}
        payload.update({
            'pct':    100,
            'result': res,
            'totals': res.get('totals'),
        })

    elif state == 'FAILURE':
        payload['error'] = str(result.result)

    return Response(payload, status=status.HTTP_200_OK)
