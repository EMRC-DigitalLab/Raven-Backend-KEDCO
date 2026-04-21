"""
energy_account/views/stations/all_stations.py

GET /api/energy-account/stations/
GET /api/energy-account/stations/<slug>/

EA analytics at the injection substation level.

Query params:
    year     : int  (default: current year)
    month    : int  (default: current month)
    state    : str  (optional — filter by state slug)
    district : str  (optional — filter by business district slug)

The station is the core billing unit — each station has exactly one
EAMonthlyReturn per month. All meter readings, feeder technical energies,
and reconciliation records tie to that return.

Methodology splits on every KPI:
  - Stream A (transformer reads) vs Stream B (feeder technical energy)
  - 33KV vs 11KV feeder type
  - Return status, billing rule, data source breakdown
  - TCN/MO reconciliation status
"""

from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from energy_account.permissions import HasEAAccess

from common.models import BusinessDistrict, InjectionSubstation
from energy_account.models import (
    EAFeederTechnicalEnergy,
    EAGridMeter,
    EAMOReconciliation,
    EAMeterCheckRecord,
    EAMeterCheckSchedule,
    EAMonthlyReading,
    EAMonthlyReturn,
    EATCNReconciliation,
)
from energy_account.utils import (
    BILLING_RULES,
    DATA_SOURCES,
    MO_STATUSES,
    RETURN_STATUSES,
    TCN_STATUSES,
    build_ea_response,
    calc_ea_metrics,
    metric,
    parse_ea_period,
)


def _station_payload(station, month, year, period):
    """
    Compute the full EA analytics payload for a single injection substation.

    Single station → single EAMonthlyReturn for the period (may not exist).
    Feeder readings and technical energy tied to that return.
    """
    try:
        monthly_return = EAMonthlyReturn.objects.select_related(
            'station', 'station__state'
        ).get(station=station, month=month, year=year)
    except EAMonthlyReturn.DoesNotExist:
        monthly_return = None

    returns_qs = EAMonthlyReturn.objects.filter(id=monthly_return.id) if monthly_return else EAMonthlyReturn.objects.none()

    # ── Core EA metrics ───────────────────────────────────────────────────────
    m  = calc_ea_metrics(returns_qs)
    ea = build_ea_response(m)

    # ── Grid meter inventory at this station ──────────────────────────────────
    transformer_meters = EAGridMeter.objects.filter(
        transformer__injection_substation=station, status='active'
    )
    feeder_meters = EAGridMeter.objects.filter(
        feeder__substation=station, status='active'
    )

    # ── Individual meter readings for this return ─────────────────────────────
    readings = []
    if monthly_return:
        for r in (
            EAMonthlyReading.objects
            .filter(monthly_return=monthly_return)
            .select_related('grid_meter', 'feeder', 'transformer')
            .order_by('grid_meter__meter_owner_type', 'grid_meter__meter_no')
        ):
            readings.append({
                'meter_no':           r.grid_meter.meter_no,
                'meter_owner_type':   r.grid_meter.meter_owner_type,
                'feeder':             r.feeder.slug if r.feeder else None,
                'transformer':        r.transformer.slug if r.transformer else None,
                'previous_reading':   float(r.previous_reading),
                'present_reading':    float(r.present_reading),
                'energy_mwh':         float(r.energy_mwh),
                'billing_mwh':        float(r.billing_mwh) if r.billing_mwh else None,
                'billing_naira':      float(r.billing_naira) if r.billing_naira else None,
                'deviation_pct':      float(r.deviation_pct) if r.deviation_pct else None,
                'is_flagged':         r.is_flagged,
                'data_source':        r.data_source,
                'billing_rule':       r.billing_rule_applied,
                'nbet_rate_snapshot': float(r.nbet_rate_snapshot) if r.nbet_rate_snapshot else None,
                'remarks':            r.remarks,
            })

    # ── Feeder technical energy (Stream B) for this return ────────────────────
    feeder_tech = []
    if monthly_return:
        for fte in (
            EAFeederTechnicalEnergy.objects
            .filter(monthly_return=monthly_return)
            .select_related('feeder')
            .order_by('feeder_type', 'feeder__slug')
        ):
            feeder_tech.append({
                'feeder':               fte.feeder.slug,
                'feeder_type':          fte.feeder_type,
                'energy_mwh':           float(fte.energy_mwh) if fte.energy_mwh else None,
                'data_completeness_pct': float(fte.data_completeness_pct),
                'days_with_data':       fte.days_with_data,
                'total_days_in_period': fte.total_days_in_period,
                'has_gaps':             fte.has_gaps,
                'gap_notes':            fte.gap_notes,
                'data_source':          fte.data_source,
            })

    # ── TCN reconciliation detail ─────────────────────────────────────────────
    tcn_detail = None
    if monthly_return:
        try:
            tcn = EATCNReconciliation.objects.get(monthly_return=monthly_return)
            tcn_detail = {
                'kedco_figure_mwh':   float(tcn.kedco_figure_mwh) if tcn.kedco_figure_mwh else None,
                'kedco_figure_naira': float(tcn.kedco_figure_naira) if tcn.kedco_figure_naira else None,
                'tcn_figure_mwh':     float(tcn.tcn_figure_mwh) if tcn.tcn_figure_mwh else None,
                'tcn_figure_naira':   float(tcn.tcn_figure_naira) if tcn.tcn_figure_naira else None,
                'difference_mwh':     float(tcn.difference_mwh) if tcn.difference_mwh else None,
                'difference_pct':     float(tcn.difference_pct) if tcn.difference_pct else None,
                'agreed_figure_mwh':  float(tcn.agreed_figure_mwh) if tcn.agreed_figure_mwh else None,
                'status':             tcn.status,
                'resolution_notes':   tcn.resolution_notes,
                'tcn_contact_name':   tcn.tcn_contact_name,
            }
        except EATCNReconciliation.DoesNotExist:
            pass

    # ── MO reconciliation detail ──────────────────────────────────────────────
    mo_detail = None
    if monthly_return:
        try:
            mo = EAMOReconciliation.objects.get(monthly_return=monthly_return)
            mo_detail = {
                'mo_figure_mwh':    float(mo.mo_figure_mwh),
                'kedco_figure_mwh': float(mo.kedco_figure_mwh),
                'difference_mwh':   float(mo.difference_mwh),
                'status':           mo.status,
                'notes':            mo.notes,
            }
        except EAMOReconciliation.DoesNotExist:
            pass

    # ── Latest meter check ────────────────────────────────────────────────────
    latest_check = (
        EAMeterCheckRecord.objects
        .filter(station=station)
        .select_related('schedule')
        .order_by('-check_date')
        .first()
    )
    meter_check_detail = None
    if latest_check:
        meter_check_detail = {
            'check_date':   str(latest_check.check_date),
            'polarity':     latest_check.polarity,
            'power_factor': float(latest_check.power_factor) if latest_check.power_factor else None,
            'voltage_r':    float(latest_check.voltage_r) if latest_check.voltage_r else None,
            'voltage_y':    float(latest_check.voltage_y) if latest_check.voltage_y else None,
            'voltage_b':    float(latest_check.voltage_b) if latest_check.voltage_b else None,
            'ct_ratio':     latest_check.ct_ratio,
            'battery_level': latest_check.battery_level,
            'risk_level':   latest_check.risk_level,
            'remarks':      latest_check.remarks,
        }

    return {
        'period': {
            'year':  period['year'],
            'month': period['month'],
            'label': period['label'],
        },
        'station': {
            'slug':  station.slug,
            'name':  station.name,
            'state': station.state.slug if station.state else None,
            'type':  station.station_type,
            'status': station.status,
        },
        'return_status': monthly_return.status if monthly_return else None,
        'is_late':       monthly_return.is_late if monthly_return else None,
        'late_reason':   monthly_return.late_reason if monthly_return else None,
        'submitted_at':  str(monthly_return.submitted_at) if monthly_return and monthly_return.submitted_at else None,

        'returns':        ea['returns'],
        'energy':         ea['energy'],
        'revenue':        ea['revenue'],
        'reconciliation': ea['reconciliation'],
        'data_quality':   ea['data_quality'],

        'meter_inventory': {
            'transformer_meters': metric(
                transformer_meters.count(),
                explanation='Active transformer billing meters at this station.',
            ),
            'feeder_meters': metric(
                feeder_meters.count(),
                explanation='Active feeder check meters at this station.',
            ),
        },

        'readings':           readings,
        'feeder_technical':   feeder_tech,
        'tcn_reconciliation': tcn_detail,
        'mo_reconciliation':  mo_detail,
        'latest_meter_check': meter_check_detail,
    }


@api_view(['GET'])
@permission_classes([HasEAAccess])
def all_stations(request):
    """
    GET /api/energy-account/stations/

    EA analytics for every injection substation, for the given billing period.
    Optional: ?state=<slug> or ?district=<slug> to filter.
    """
    period = parse_ea_period(request)
    month, year   = period['month'], period['year']
    state_slug    = request.GET.get('state', '').strip()
    district_slug = request.GET.get('district', '').strip()

    # Bulk aggregation from returns — avoids N+1
    returns_base = EAMonthlyReturn.objects.filter(month=month, year=year)

    stations_qs = InjectionSubstation.objects.select_related('state').order_by('state__name', 'name')
    if state_slug:
        stations_qs = stations_qs.filter(state__slug=state_slug)
    if district_slug:
        district = get_object_or_404(BusinessDistrict, slug=district_slug)
        station_ids = list(
            InjectionSubstation.objects
            .filter(feeders__business_district=district)
            .values_list('id', flat=True)
            .distinct()
        )
        stations_qs = stations_qs.filter(id__in=station_ids)

    # Prefetch bulk aggregations for efficiency
    # Returns aggregate per station
    from django.db.models import F
    station_return_agg = {
        row['station_id']: row
        for row in (
            returns_base
            .values('station_id')
            .annotate(
                total_mwh=Sum('station_billing_mwh'),
                total_naira=Sum('station_billing_naira'),
                late_count=Count('id', filter=Q(is_late=True)),
                total_returns=Count('id'),
                status=F('status'),
            )
        )
    }

    # Feeder technical energy per station
    station_tech_agg = {
        row['station_id']: row
        for row in (
            EAFeederTechnicalEnergy.objects
            .filter(monthly_return__month=month, monthly_return__year=year)
            .values('station_id')
            .annotate(
                stream_b_mwh=Sum('energy_mwh'),
                kv33_mwh=Sum('energy_mwh', filter=Q(feeder_type='33KV')),
                kv11_mwh=Sum('energy_mwh', filter=Q(feeder_type='11KV')),
                avg_completeness=Avg('data_completeness_pct'),
                feeders_with_gaps=Count('id', filter=Q(has_gaps=True)),
            )
        )
    }

    # Stream A per station via readings
    station_stream_a = {
        row['monthly_return__station_id']: row
        for row in (
            EAMonthlyReading.objects
            .filter(
                monthly_return__month=month,
                monthly_return__year=year,
                grid_meter__meter_owner_type='transformer',
            )
            .values('monthly_return__station_id')
            .annotate(
                stream_a_mwh=Sum('billing_mwh'),
                stream_a_naira=Sum('billing_naira'),
                flagged_count=Count('id', filter=Q(is_flagged=True)),
            )
        )
    }

    results = []
    for station in stations_qs:
        sid = station.id
        r   = station_return_agg.get(sid, {})
        t   = station_tech_agg.get(sid, {})
        a   = station_stream_a.get(sid, {})

        if not r:
            continue  # no return for this period — skip

        total_mwh   = float(r.get('total_mwh')   or 0)
        total_naira = float(r.get('total_naira') or 0)
        stream_b    = float(t.get('stream_b_mwh') or 0)
        stream_a    = float(a.get('stream_a_mwh') or 0)
        deviation   = round(abs(stream_a - stream_b) / stream_b * 100, 4) if stream_b else None

        results.append({
            'station': {
                'slug':  station.slug,
                'name':  station.name,
                'state': station.state.slug if station.state else None,
                'type':  station.station_type,
            },
            'return_status': r.get('status'),
            'is_late':       bool(r.get('late_count', 0)),

            'energy': {
                'total_billing_mwh': metric(
                    total_mwh,
                    unit='MWh',
                    explanation='Billed energy from the station monthly return.',
                ),
                'stream_a_mwh': metric(
                    stream_a,
                    unit='MWh',
                    explanation='Stream A: transformer meter billing_mwh for this station.',
                ),
                'stream_b_mwh': metric(
                    stream_b,
                    unit='MWh',
                    explanation='Stream B: total feeder technical energy from the monitoring system.',
                ),
                'stream_a_vs_b_deviation': metric(
                    deviation,
                    unit='%',
                    explanation='|Stream A − Stream B| / Stream B × 100.',
                ),
                'kv33_mwh': metric(
                    float(t.get('kv33_mwh') or 0),
                    unit='MWh',
                    explanation='Stream B energy from 33kV feeders.',
                ),
                'kv11_mwh': metric(
                    float(t.get('kv11_mwh') or 0),
                    unit='MWh',
                    explanation='Stream B energy from 11kV feeders.',
                ),
                'avg_data_completeness': metric(
                    round(float(t.get('avg_completeness') or 0), 2),
                    unit='%',
                    explanation='Average data completeness % across feeder technical energy records.',
                ),
                'feeders_with_gaps': metric(
                    t.get('feeders_with_gaps', 0),
                    explanation='Feeders with incomplete Stream B data for this period.',
                ),
                'flagged_readings': metric(
                    a.get('flagged_count', 0),
                    explanation='Readings flagged for anomalies at this station.',
                ),
            },
            'revenue': {
                'total_billing_naira': metric(
                    total_naira,
                    unit='NGN',
                    explanation='Total billed Naira from the station monthly return.',
                ),
                'stream_a_naira': metric(
                    float(a.get('stream_a_naira') or 0),
                    unit='NGN',
                    explanation='Naira value of Stream A transformer readings at NBET rate snapshot.',
                ),
            },
        })

    return Response({
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },
        'stations': results,
    })


from django.shortcuts import get_object_or_404


@api_view(['GET'])
@permission_classes([HasEAAccess])
def single_station(request, slug):
    """
    GET /api/energy-account/stations/<slug>/

    Full EA analytics for a single injection substation.
    Includes individual meter readings, feeder technical energy,
    TCN/MO reconciliation detail, and latest meter check record.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    station = get_object_or_404(InjectionSubstation, slug=slug)

    return Response(_station_payload(station, month, year, period))
