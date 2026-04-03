"""
hr/views/dso_compliance/views.py

DSO Submission Compliance — HR management metric.

Tracks whether field officers (DSOs) submitted hourly load data and daily
energy readings on time or late, as recorded by DataNest.

Endpoints:
    GET /hr/dso-compliance/overview/
    GET /hr/dso-compliance/states/
    GET /hr/dso-compliance/districts/
    GET /hr/dso-compliance/feeders/
    GET /hr/dso-compliance/bands/

All endpoints accept the same query params as the rest of Raven:
    ?mode=monthly&year=2026&month=4   (returns last 4 months)
    ?mode=weekly&from_date=2026-04-01 (returns last 4 weeks)
    ?mode=daily&from_date=2026-04-03  (returns last 4 days)
    ?mode=yearly&year=2026            (returns last 4 years)

Each period returns:
    - hourly: total DSO submissions, on_time, late, compliance_rate
    - daily_energy: same for daily energy meter readings
"""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.db.models import Count, Q
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import Band, BusinessDistrict, Feeder, State
from technical.models import CumulativeMeterReading, HourlyLoad


# ── Period helpers ────────────────────────────────────────────────────────────

def _parse_anchor(request):
    """Return (mode, anchor_date) from request params."""
    from datetime import datetime
    mode = request.GET.get('mode', 'monthly')
    today = date.today()

    if mode == 'monthly':
        year  = int(request.GET.get('year',  today.year))
        month = int(request.GET.get('month', today.month))
        anchor = date(year, month, 1)

    elif mode == 'weekly':
        raw = request.GET.get('from_date', str(today))
        try:
            anchor = date.fromisoformat(raw[:10])
        except ValueError:
            anchor = today
        # Snap to Monday of that week
        anchor = anchor - timedelta(days=anchor.weekday())

    elif mode == 'yearly':
        year   = int(request.GET.get('year', today.year))
        anchor = date(year, 1, 1)

    else:  # daily
        raw = request.GET.get('from_date', str(today))
        try:
            anchor = date.fromisoformat(raw[:10])
        except ValueError:
            anchor = today

    return mode, anchor


def _last_4_periods(mode, anchor):
    """
    Return list of last 4 periods (including anchor) as:
    [{'label': str, 'start': date, 'end': date}, ...]
    Ordered oldest → newest.
    """
    today   = date.today()
    periods = []

    for i in range(3, -1, -1):   # 3, 2, 1, 0  → oldest first
        if mode == 'monthly':
            start = anchor - relativedelta(months=i)
            end   = min(start + relativedelta(months=1) - timedelta(days=1), today)
            label = start.strftime('%B %Y')

        elif mode == 'weekly':
            start = anchor - timedelta(weeks=i)
            end   = min(start + timedelta(days=6), today)
            label = f"{start} to {end}"

        elif mode == 'yearly':
            start = date(anchor.year - i, 1, 1)
            end   = min(date(anchor.year - i, 12, 31), today)
            label = str(anchor.year - i)

        else:  # daily
            start = anchor - timedelta(days=i)
            end   = start
            label = str(start)

        periods.append({'label': label, 'start': start, 'end': end})

    return periods


# ── Core compliance calculator ────────────────────────────────────────────────

def _compliance(feeder_qs, start, end):
    """
    Calculate DSO submission compliance for a set of feeders and a date range.
    Only counts submissions where submission_type='dso'.
    Admin overrides are excluded — they are corrections, not field officer submissions.

    Returns:
        {
            'hourly':       {total, on_time, late, compliance_rate},
            'daily_energy': {total, on_time, late, compliance_rate},
        }
    """
    # Hourly load
    h = (
        HourlyLoad.objects
        .filter(feeder__in=feeder_qs, date__gte=start, date__lte=end, submission_type='dso')
        .aggregate(total=Count('id'), late=Count('id', filter=Q(is_late=True)))
    )
    h_total   = h['total'] or 0
    h_late    = h['late']  or 0
    h_on_time = h_total - h_late
    h_rate    = round(h_on_time / h_total * 100, 1) if h_total else None

    # Daily energy meter readings
    d = (
        CumulativeMeterReading.objects
        .filter(feeder__in=feeder_qs, reading_date__gte=start, reading_date__lte=end, submission_type='dso')
        .aggregate(total=Count('id'), late=Count('id', filter=Q(is_late=True)))
    )
    d_total   = d['total'] or 0
    d_late    = d['late']  or 0
    d_on_time = d_total - d_late
    d_rate    = round(d_on_time / d_total * 100, 1) if d_total else None

    return {
        'hourly': {
            'total_submissions': h_total,
            'on_time':           h_on_time,
            'late':              h_late,
            'compliance_rate':   h_rate,
            'explanation': (
                'Count of hourly load entries submitted by DSO field officers in this period. '
                'On-time = submitted within the allowed window set by DataNest. '
                'Admin overrides are excluded.'
            ),
        },
        'daily_energy': {
            'total_submissions': d_total,
            'on_time':           d_on_time,
            'late':              d_late,
            'compliance_rate':   d_rate,
            'explanation': (
                'Count of daily energy meter readings submitted by DSO field officers in this period. '
                'On-time = submitted within the allowed window set by DataNest. '
                'Admin overrides are excluded.'
            ),
        },
    }


def _period_compliance(feeder_qs, periods):
    """Run _compliance for each period and return list of results."""
    return [
        {
            'label': p['label'],
            'start': str(p['start']),
            'end':   str(p['end']),
            **_compliance(feeder_qs, p['start'], p['end']),
        }
        for p in periods
    ]


# ── Views ─────────────────────────────────────────────────────────────────────

@api_view(['GET'])
def dso_compliance_overview(request):
    """
    DSO submission compliance across ALL feeders.
    Returns last 4 periods of the requested mode.
    """
    mode, anchor = _parse_anchor(request)
    periods      = _last_4_periods(mode, anchor)
    feeders      = Feeder.objects.filter(is_onboarded=True)

    return Response({
        'mode':    mode,
        'periods': _period_compliance(feeders, periods),
    })


@api_view(['GET'])
def dso_compliance_states(request):
    """
    DSO submission compliance grouped by state.
    Returns last 4 periods per state.
    """
    mode, anchor = _parse_anchor(request)
    periods      = _last_4_periods(mode, anchor)
    states       = State.objects.all().order_by('name')

    result = []
    for state in states:
        feeders = Feeder.objects.filter(
            is_onboarded=True,
            business_district__state=state,
        )
        result.append({
            'state':   state.name,
            'periods': _period_compliance(feeders, periods),
        })

    return Response({'mode': mode, 'states': result})


@api_view(['GET'])
def dso_compliance_districts(request):
    """
    DSO submission compliance grouped by business district.
    Optionally filter by state: ?state=Kano
    Returns last 4 periods per district.
    """
    mode, anchor  = _parse_anchor(request)
    periods       = _last_4_periods(mode, anchor)
    state_filter  = request.GET.get('state')

    districts_qs = BusinessDistrict.objects.all().order_by('name')
    if state_filter:
        districts_qs = districts_qs.filter(state__name=state_filter)

    result = []
    for district in districts_qs:
        feeders = Feeder.objects.filter(
            is_onboarded=True,
            business_district=district,
        )
        result.append({
            'district': district.name,
            'state':    district.state.name if district.state else None,
            'periods':  _period_compliance(feeders, periods),
        })

    return Response({'mode': mode, 'districts': result})


@api_view(['GET'])
def dso_compliance_feeders(request):
    """
    DSO submission compliance per individual feeder.
    Optionally filter by: ?state=Kano  ?district=Kano  ?band=A  ?voltage=11kv
    Returns last 4 periods per feeder.
    """
    mode, anchor     = _parse_anchor(request)
    periods          = _last_4_periods(mode, anchor)
    state_filter     = request.GET.get('state')
    district_filter  = request.GET.get('district')
    band_filter      = request.GET.get('band')
    voltage_filter   = request.GET.get('voltage')

    feeders_qs = Feeder.objects.filter(is_onboarded=True).select_related(
        'band', 'business_district__state'
    ).order_by('name')

    if state_filter:
        feeders_qs = feeders_qs.filter(business_district__state__name=state_filter)
    if district_filter:
        feeders_qs = feeders_qs.filter(business_district__name=district_filter)
    if band_filter:
        feeders_qs = feeders_qs.filter(band__name=band_filter)
    if voltage_filter:
        feeders_qs = feeders_qs.filter(voltage_level__iexact=voltage_filter)

    result = []
    for feeder in feeders_qs:
        result.append({
            'feeder':   feeder.name,
            'slug':     feeder.slug,
            'band':     feeder.band.name if feeder.band else None,
            'district': feeder.business_district.name if feeder.business_district else None,
            'state':    feeder.business_district.state.name if feeder.business_district and feeder.business_district.state else None,
            'periods':  _period_compliance(Feeder.objects.filter(id=feeder.id), periods),
        })

    return Response({'mode': mode, 'feeders': result})


@api_view(['GET'])
def dso_compliance_bands(request):
    """
    DSO submission compliance grouped by service band (A, B, C, D, E).
    Returns last 4 periods per band.
    """
    mode, anchor = _parse_anchor(request)
    periods      = _last_4_periods(mode, anchor)
    bands        = Band.objects.all().order_by('name')

    result = []
    for band in bands:
        feeders = Feeder.objects.filter(is_onboarded=True, band=band)
        result.append({
            'band':    band.name,
            'periods': _period_compliance(feeders, periods),
        })

    return Response({'mode': mode, 'bands': result})
