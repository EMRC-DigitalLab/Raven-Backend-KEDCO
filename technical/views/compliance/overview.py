# technical/views/compliance/overview.py
"""
GET /api/technical/compliance/overview/

Returns total + per-band compliance summary.
Shows how many feeders per band are compliant / at_risk / critical / upgrade_eligible.

Filters: mode, year, month, from_date, to_date, voltage_level (11kv|33kv),
         state, district (business district name)
"""
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import Band, Feeder
from technical.utils.compliance_utils import (
    BAND_ORDER,
    BAND_TARGET_HOURS,
    _cap_to_today,
    _date_range,
    bulk_daily_hours,
    build_period_object,
    compute_compliance,
)


def _parse_request_dates(request):
    """Parse period params → (from_date, to_date, mode). Mirrors existing pattern."""
    mode = request.GET.get('mode', 'monthly')
    today = datetime.now().date()

    if mode == 'yearly':
        year = int(request.GET.get('year', today.year))
        return datetime(year, 1, 1).date(), datetime(year, 12, 31).date(), 'yearly'

    if mode in ('daily', 'weekly', 'custom', 'range'):
        from_str = request.GET.get('from_date')
        to_str = request.GET.get('to_date', from_str)
        if not from_str:
            raise ValueError('from_date is required for this mode')
        from_date = datetime.strptime(from_str[:10], '%Y-%m-%d').date()
        to_date = datetime.strptime(to_str[:10], '%Y-%m-%d').date()
        return from_date, to_date, mode

    # monthly (default)
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))
    from_date = datetime(year, month, 1).date()
    to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
    return from_date, to_date, 'monthly'


@api_view(['GET'])
def compliance_overview(request):
    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    # Cap end at today — no future data
    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)

    # ── Filters ──────────────────────────────────────────────────────────────
    voltage_level = request.GET.get('voltage_level') or request.GET.get('feeder_type')
    if voltage_level and voltage_level not in ('11kv', '33kv'):
        voltage_level = None
    state = request.GET.get('state')
    district = request.GET.get('district')

    # ── Fetch onboarded feeders with band ────────────────────────────────────
    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True)
        .select_related('band', 'business_district__state')
        .exclude(band__isnull=True)
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if district:
        feeders_qs = feeders_qs.filter(business_district__name__iexact=district)
    elif state:
        feeders_qs = feeders_qs.filter(business_district__state__slug__iexact=state)

    feeders = list(feeders_qs)
    if not feeders:
        return Response({
            'period': build_period_object(from_date, to_date, mode),
            'filters': {'voltage_level': voltage_level, 'state': state, 'district': district},
            'summary': {'total_feeders': 0, 'compliant': 0, 'at_risk': 0, 'critical': 0, 'upgrade_eligible': 0},
            'by_band': [],
        })

    # ── One bulk query for all feeders ───────────────────────────────────────
    feeder_ids = [f.id for f in feeders]
    daily_map = bulk_daily_hours(feeder_ids, from_date, to_date)

    # ── Aggregate per band ───────────────────────────────────────────────────
    # bands keyed by slug
    bands_meta = {}
    for feeder in feeders:
        slug = feeder.band.slug
        if slug not in bands_meta:
            bands_meta[slug] = {
                'band': {
                    'slug': slug,
                    'name': feeder.band.name,
                    'target_hours_per_day': float(feeder.band.target_hours_per_day),
                },
                'total': 0,
                'compliant': 0,
                'at_risk': 0,
                'critical': 0,
                'upgrade_eligible': 0,
                'total_compliance_pct': 0.0,
            }

        target = BAND_TARGET_HOURS.get(slug, float(feeder.band.target_hours_per_day))
        c = compute_compliance(daily_map.get(feeder.id, {}), slug, target, period_dates)

        bands_meta[slug]['total'] += 1
        bands_meta[slug]['total_compliance_pct'] += c['compliance_pct']

        status = c['status']
        if status == 'upgrade_eligible':
            bands_meta[slug]['upgrade_eligible'] += 1
        elif status == 'critical':
            bands_meta[slug]['critical'] += 1
        elif status == 'at_risk':
            bands_meta[slug]['at_risk'] += 1
        else:
            bands_meta[slug]['compliant'] += 1

    # ── Build ordered by_band list (A → E) ───────────────────────────────────
    by_band = []
    for slug in sorted(bands_meta, key=lambda s: BAND_ORDER.get(s, 99)):
        b = bands_meta[slug]
        total = b['total']
        avg_pct = round(b['total_compliance_pct'] / total, 1) if total else 0.0
        by_band.append({
            'band': b['band'],
            'total': total,
            'compliant': b['compliant'],
            'at_risk': b['at_risk'],
            'critical': b['critical'],
            'upgrade_eligible': b['upgrade_eligible'],
            'avg_compliance_pct': avg_pct,
        })

    # ── Top-level summary ────────────────────────────────────────────────────
    summary = {
        'total_feeders': sum(b['total'] for b in by_band),
        'compliant': sum(b['compliant'] for b in by_band),
        'at_risk': sum(b['at_risk'] for b in by_band),
        'critical': sum(b['critical'] for b in by_band),
        'upgrade_eligible': sum(b['upgrade_eligible'] for b in by_band),
    }

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {
            'voltage_level': voltage_level or 'all',
            'state': state,
            'district': district,
        },
        'summary': summary,
        'by_band': by_band,
    })
