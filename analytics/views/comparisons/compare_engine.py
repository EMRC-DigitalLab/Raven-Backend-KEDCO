"""
analytics/views/comparisons/compare_engine.py

Two endpoints:

  GET  /api/analytics/compare/available/
       Returns the full metrics catalogue filtered to what the requesting
       user can actually access. Frontend uses this to build the compare UI.

  POST /api/analytics/compare/
       Runs a comparison.

       Mode 1 — entities  (compare many entities, same time window)
       Mode 2 — periods   (compare one entity across multiple time windows)

───────────────────────────────────────────────────────────────────────────────
ENTITY COMPARISON BODY
───────────────────────────────────────────────────────────────────────────────
{
    "compare_mode": "entities",
    "entity_type":  "district",          // state | district | feeder | band
    "entity_ids":   ["<uuid>", "<uuid>"],
    "metrics":      ["hours_of_supply", "energy_delivered", "revenue_billed"],
    "from_date":    "2025-01-01",
    "to_date":      "2025-03-31",
    "granularity":  "monthly",           // daily | weekly | monthly | yearly
    "feeder_type":  "11kv",              // optional — 11kv | 33kv
    "include_trend": true                // optional, default true
}

───────────────────────────────────────────────────────────────────────────────
PERIOD COMPARISON BODY
───────────────────────────────────────────────────────────────────────────────
{
    "compare_mode": "periods",
    "entity_type":  "feeder",
    "entity_id":    "<uuid>",
    "metrics":      ["hours_of_supply", "peak_load", "total_interruptions"],
    "feeder_type":  "11kv",
    "periods": [
        {"label": "Today",      "from_date": "2025-03-28", "to_date": "2025-03-28"},
        {"label": "Yesterday",  "from_date": "2025-03-27", "to_date": "2025-03-27"},
        {"label": "Last Month", "from_date": "2025-02-01", "to_date": "2025-02-28"}
    ]
}

───────────────────────────────────────────────────────────────────────────────
ACCESS CONTROL
───────────────────────────────────────────────────────────────────────────────
Metrics from modules the user cannot access are silently moved to
`metrics_denied` in the response — the visible data is never contaminated.
The frontend should read `metrics_denied` and grey-out / hide those fields.
"""

import logging
from datetime import datetime

from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)

from analytics.compare_metrics import METRICS_BY_MODULE
from analytics.services.compare_service import (
    compare_customers,
    compare_entities,
    compare_periods,
    get_user_accessible_modules,
)

VALID_ENTITY_TYPES  = ('state', 'district', 'feeder', 'band', 'station')
VALID_GRANULARITIES = ('daily', 'weekly', 'monthly', 'yearly', 'custom')


@api_view(['GET'])
def available_metrics(request):
    """
    GET /api/analytics/compare/available/

    Returns metric catalogue scoped to the requesting user's module access.
    Frontend renders this as the compare-builder picker.
    """
    user       = request.user
    accessible = get_user_accessible_modules(user)

    # Only include modules this user can access
    accessible_metrics: dict = {
        module: metric_list
        for module, metric_list in METRICS_BY_MODULE.items()
        if module in accessible
    }

    return Response({
        'entity_types': [
            {'key': 'state',    'label': 'States',                       'icon': 'map'},
            {'key': 'district', 'label': 'Business Districts',           'icon': 'building'},
            {'key': 'feeder',   'label': 'Feeders',                      'icon': 'zap'},
            {'key': 'band',     'label': 'Service Bands (A–E)',          'icon': 'layers'},
            {'key': 'station',  'label': 'Injection Substations (EA)',   'icon': 'activity',
             'note': 'Energy Account billing entity — uses month/year period, not date range'},
        ],
        'granularities': [
            {'key': 'daily',   'label': 'Daily',   'description': 'One data point per day'},
            {'key': 'weekly',  'label': 'Weekly',  'description': 'One data point per calendar week'},
            {'key': 'monthly', 'label': 'Monthly', 'description': 'One data point per calendar month'},
            {'key': 'yearly',  'label': 'Yearly',  'description': 'One data point per year'},
        ],
        'feeder_types': [
            {'key': '11kv', 'label': '11kV Feeders'},
            {'key': '33kv', 'label': '33kV Feeders'},
        ],
        'compare_modes': [
            {
                'key': 'entities',
                'label': 'Compare Entities',
                'description': 'Compare multiple feeders / districts / states against each other',
            },
            {
                'key': 'periods',
                'label': 'Compare Periods',
                'description': 'Compare one entity across different time periods (today vs yesterday, etc.)',
            },
        ],
        'metrics': accessible_metrics,
        'accessible_modules': sorted(accessible),
    })


@api_view(['POST'])
def run_compare(request):
    """POST /api/analytics/compare/"""
    user = request.user
    body = request.data

    compare_mode = body.get('compare_mode', 'entities')
    entity_type  = body.get('entity_type', '')
    metrics      = body.get('metrics', [])
    feeder_type  = body.get('feeder_type') or None

    # ── Basic validation ───────────────────────────────────────────────────────
    if entity_type not in VALID_ENTITY_TYPES:
        return Response(
            {'error': f"Invalid entity_type '{entity_type}'. "
                      f"Must be one of: {', '.join(VALID_ENTITY_TYPES)}"},
            status=400,
        )

    if not metrics or not isinstance(metrics, list):
        return Response({'error': 'metrics must be a non-empty list.'}, status=400)

    # ── Route by mode ──────────────────────────────────────────────────────────
    if compare_mode == 'periods':
        return _handle_periods(user, entity_type, metrics, feeder_type, body)
    else:
        return _handle_entities(user, entity_type, metrics, feeder_type, body)


# ─── Handlers ─────────────────────────────────────────────────────────────────

def _handle_entities(user, entity_type, metrics, feeder_type, body):
    entity_ids    = body.get('entity_ids', [])
    from_date_str = body.get('from_date', '')
    to_date_str   = body.get('to_date', '')
    granularity   = body.get('granularity', 'monthly')
    include_trend = body.get('include_trend', True)

    if not entity_ids or not isinstance(entity_ids, list):
        return Response({'error': 'entity_ids must be a non-empty list.'}, status=400)

    if not from_date_str or not to_date_str:
        return Response({'error': 'from_date and to_date are required.'}, status=400)

    try:
        from_date = datetime.strptime(str(from_date_str)[:10], '%Y-%m-%d').date()
        to_date   = datetime.strptime(str(to_date_str)[:10],   '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    if from_date > to_date:
        return Response({'error': 'from_date must be on or before to_date.'}, status=400)

    if granularity not in VALID_GRANULARITIES:
        granularity = 'monthly'

    try:
        result = compare_entities(
            user          = user,
            entity_type   = entity_type,
            entity_ids    = entity_ids,
            metrics       = metrics,
            from_date     = from_date,
            to_date       = to_date,
            feeder_type   = feeder_type,
            granularity   = granularity,
            include_trend = bool(include_trend),
        )
    except Exception as exc:
        logger.exception("compare_entities failed: %s", exc)
        return Response({'error': str(exc)}, status=500)

    if 'error' in result:
        return Response(result, status=400)
    return Response(result)


def _handle_periods(user, entity_type, metrics, feeder_type, body):
    entity_id = body.get('entity_id')
    periods   = body.get('periods', [])

    if not entity_id:
        return Response({'error': 'entity_id is required for period comparison.'}, status=400)

    if not periods or not isinstance(periods, list):
        return Response(
            {'error': 'periods must be a non-empty list of {label, from_date, to_date}.'},
            status=400,
        )

    try:
        result = compare_periods(
            user        = user,
            entity_type = entity_type,
            entity_id   = entity_id,
            metrics     = metrics,
            periods     = periods,
            feeder_type = feeder_type,
        )
    except Exception as exc:
        logger.exception("compare_periods failed: %s", exc)
        return Response({'error': str(exc)}, status=500)

    if 'error' in result:
        return Response(result, status=400)
    return Response(result)


# ─── Customer consumption comparison ──────────────────────────────────────────

VALID_CUSTOMER_TYPES = ('MDI', 'MDNI', 'all')
VALID_SCOPE_TYPES    = ('feeder', 'district', 'state')
VALID_SORT_BY        = ('current_consumption', 'variance_pct', 'decline')


@api_view(['POST'])
def customer_compare(request):
    """
    POST /api/analytics/compare/customers/

    Compare top-N MDI or MDNI customers by consumption across two time periods.

    Body:
    {
        "customer_type":    "MDI",               // "MDI" | "MDNI" | "all"
        "current_period":   {"from_date": "2025-01-13", "to_date": "2025-01-26"},
        "previous_period":  {"from_date": "2024-12-01", "to_date": "2024-12-31"},
        "scope_type":       "feeder",             // optional — "feeder" | "district" | "state"
        "scope_id":         "<uuid>",             // optional — required if scope_type is set
        "top_n":            50,                   // optional, default 50 (max 200)
        "sort_by":          "current_consumption" // optional
    }

    Returns top-N customers with:
      current_consumption_kwh, previous_consumption_kwh,
      variance_kwh, variance_pct, trend label, is_bypass flag.
    """
    body = request.data

    customer_type = body.get('customer_type', 'all')
    if customer_type not in VALID_CUSTOMER_TYPES:
        return Response(
            {'error': f"customer_type must be one of: {', '.join(VALID_CUSTOMER_TYPES)}"},
            status=400,
        )

    # ── Parse periods ──────────────────────────────────────────────────────────
    current_period  = body.get('current_period', {})
    previous_period = body.get('previous_period', {})

    try:
        current_from  = datetime.strptime(str(current_period['from_date'])[:10],  '%Y-%m-%d').date()
        current_to    = datetime.strptime(str(current_period['to_date'])[:10],    '%Y-%m-%d').date()
        previous_from = datetime.strptime(str(previous_period['from_date'])[:10], '%Y-%m-%d').date()
        previous_to   = datetime.strptime(str(previous_period['to_date'])[:10],   '%Y-%m-%d').date()
    except (KeyError, ValueError, TypeError):
        return Response(
            {'error': 'current_period and previous_period must include from_date and to_date in YYYY-MM-DD format.'},
            status=400,
        )

    if current_from > current_to or previous_from > previous_to:
        return Response({'error': 'from_date must be on or before to_date in each period.'}, status=400)

    # ── Scope (optional) ───────────────────────────────────────────────────────
    scope_type = body.get('scope_type') or None
    scope_id   = body.get('scope_id')   or None

    if scope_type and scope_type not in VALID_SCOPE_TYPES:
        return Response(
            {'error': f"scope_type must be one of: {', '.join(VALID_SCOPE_TYPES)}"},
            status=400,
        )
    if scope_type and not scope_id:
        return Response({'error': 'scope_id is required when scope_type is provided.'}, status=400)

    # ── Options ────────────────────────────────────────────────────────────────
    try:
        top_n = min(int(body.get('top_n', 50)), 200)
    except (TypeError, ValueError):
        top_n = 50

    sort_by = body.get('sort_by', 'current_consumption')
    if sort_by not in VALID_SORT_BY:
        sort_by = 'current_consumption'

    try:
        result = compare_customers(
            user          = request.user,
            current_from  = current_from,
            current_to    = current_to,
            previous_from = previous_from,
            previous_to   = previous_to,
            customer_type = customer_type,
            scope_type    = scope_type,
            scope_id      = scope_id,
            top_n         = top_n,
            sort_by       = sort_by,
        )
    except Exception as exc:
        logger.exception("compare_customers failed: %s", exc)
        return Response({'error': str(exc)}, status=500)

    if 'error' in result:
        return Response(result, status=400)
    return Response(result)
