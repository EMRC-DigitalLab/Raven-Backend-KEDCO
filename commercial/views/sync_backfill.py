"""
commercial/views/sync_backfill.py

POST /api/commercial/sync/backfill/
    Trigger a commercial data backfill from DataNest into Raven.
    Admin / super_admin only.
    Body (JSON):
        {
            "start_date": "2026-01-01",   // required (for readings)
            "end_date":   "2026-05-31",   // required (for readings)
            "tables": ["readings", "customers", "managers", "tariff_rates"]
                                          // optional, default: all four
        }
    Response 202:
        {
            "job_id":     "<celery-task-uuid>",
            "status_url": "/api/commercial/sync/backfill/<job_id>/",
            "message":    "..."
        }

GET /api/commercial/sync/backfill/<job_id>/
    Poll job progress.  Any authenticated user.
    Response 200:
        {
            "job_id":  "<uuid>",
            "state":   "PENDING" | "PROGRESS" | "SUCCESS" | "FAILURE",
            "pct":     75,
            "current_table": "readings",
            "totals":  { readings: {...}, customers: {...}, ... },
            "result":  { ... }   // only when state == SUCCESS
        }
"""

import datetime

from celery.result import AsyncResult
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

VALID_TABLES = {'readings', 'customers', 'managers', 'tariff_rates'}


def _can_trigger(user):
    if getattr(user, 'role', None) in ('super_admin', 'admin'):
        return True
    from users.models import UserSectionAccess
    return UserSectionAccess.objects.filter(
        user=user,
        section__name='commercial',
        is_active=True,
    ).exists()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_backfill(request):
    if not _can_trigger(request.user):
        return Response(
            {'error': 'Only admin or super_admin users can trigger a backfill.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    data = request.data

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

    if (end_date - start_date).days > 366:
        return Response(
            {'error': 'Date range cannot exceed 366 days per request.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    raw_tables = data.get('tables', list(VALID_TABLES))
    if isinstance(raw_tables, str):
        raw_tables = [t.strip() for t in raw_tables.split(',')]
    tables = [t for t in raw_tables if t in VALID_TABLES]
    if not tables:
        return Response(
            {'error': f'tables must include at least one of: {sorted(VALID_TABLES)}.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from commercial.tasks import backfill_commercial_data_task

    task = backfill_commercial_data_task.apply_async(
        kwargs={
            'start_date_str': str(start_date),
            'end_date_str':   str(end_date),
            'tables':         tables,
        },
        queue='datanest_sync',
    )

    status_url = f'/api/commercial/sync/backfill/{task.id}/'

    return Response(
        {
            'job_id':     task.id,
            'status_url': status_url,
            'message': (
                f'Commercial backfill queued for {start_date} to {end_date} '
                f'(tables={tables}). Poll status_url for progress.'
            ),
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def backfill_status(request, job_id):
    result = AsyncResult(job_id)
    state  = result.state

    payload = {
        'job_id':        job_id,
        'state':         state,
        'pct':           0,
        'current_table': None,
        'totals':        None,
        'result':        None,
        'error':         None,
    }

    if state == 'PROGRESS':
        meta = result.info or {}
        payload.update({
            'pct':           meta.get('pct', 0),
            'current_table': meta.get('current_table'),
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
