from datetime import datetime, timedelta

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.utils import timezone


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def _fault_label(code: str) -> str:
    """Return the full human-readable name for a fault type code, e.g. 'E/F' → 'Earth Fault'."""
    from technical.models import FeederInterruption
    _map = dict(FeederInterruption.INTERRUPTION_TYPES)
    return _map.get(code, code)


def _named_breakdown(breakdown_by_type: dict, limit: int = 5) -> dict:
    """Convert a fault code breakdown dict to use full names, sorted by count, top N."""
    sorted_items = sorted(breakdown_by_type.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]
    return {
        _fault_label(code): stats
        for code, stats in sorted_items
    }


def _normalise_voltage(v: str) -> str | None:
    """Normalise '11kv', '11KV', '11', '33kv', '33' etc. to '11kv' or '33kv'."""
    if not v:
        return None
    v = v.strip().lower().replace(' ', '')
    if v in ('11kv', '11'):
        return '11kv'
    if v in ('33kv', '33'):
        return '33kv'
    return None


def _feeder_filter(feeder: str = None, district: str = None, state: str = None, voltage_level: str = None, band: str = None) -> dict:
    from common.models import Band, BusinessDistrict, Feeder, State

    f: dict = {}
    if feeder:
        obj = Feeder.objects.filter(Q(slug=feeder) | Q(name__icontains=feeder)).first()
        if obj:
            f['feeder'] = obj
    if district and 'feeder' not in f:
        obj = BusinessDistrict.objects.filter(Q(slug=district) | Q(name__icontains=district)).first()
        if obj:
            f['feeder__business_district'] = obj
    if state and 'feeder' not in f and 'feeder__business_district' not in f:
        obj = State.objects.filter(Q(slug=state) | Q(name__icontains=state)).first()
        if obj:
            districts = BusinessDistrict.objects.filter(state=obj)
            f['feeder__business_district__in'] = districts
    vl = _normalise_voltage(voltage_level)
    if vl:
        f['feeder__voltage_level'] = vl
    if band and 'feeder' not in f:
        band_obj = Band.objects.filter(name__iexact=band.strip().upper()).first()
        if band_obj:
            f['feeder__band'] = band_obj
    return f


def _hos_from_hourly_load(base: dict, start, end) -> dict:
    """Compute hours of supply from DSO-submitted HourlyLoad entries (submission_type='dso', load_mw > 0).
    All aggregation done in the database — no Python arithmetic."""
    from technical.models import HourlyLoad

    per_feeder_day = (
        HourlyLoad.objects
        .filter(**base, date__gte=start, date__lte=end, load_mw__gt=0)
        .values('feeder_id', 'date')
        .annotate(supply_hours=Count('hour', distinct=True))
    )

    if not per_feeder_day.exists():
        return {
            'avg_daily_hours': None,
            'records_count': 0,
            'source': 'hourly_load',
            'note': 'No hourly load readings found in this period',
        }

    agg = per_feeder_day.aggregate(
        avg_daily_hours=Avg('supply_hours'),
        feeder_day_count=Count('feeder_id'),
    )

    return {
        'avg_daily_hours': round(float(agg['avg_daily_hours'] or 0), 2),
        'records_count': agg['feeder_day_count'],
        'source': 'hourly_load',
        'note': 'Hours derived from hourly load readings (load_mw > 0 per hour)',
    }


def query_technical(start_date: str, end_date: str, feeder: str = None, district: str = None, state: str = None, voltage_level: str = None, band: str = None) -> dict:
    """Return technical metrics: hours of supply, energy delivered, interruptions."""
    from technical.models import (
        DailyHoursOfSupply,
        EnergyDelivered,
        FeederEnergyDaily,
        FeederInterruption,
        calculate_interruption_metrics,
    )

    start, end = _parse(start_date), _parse(end_date)
    base = _feeder_filter(feeder, district, state, voltage_level, band)

    # ── Hours of supply: DailyHoursOfSupply first, fall back to HourlyLoad ──
    hos_qs = DailyHoursOfSupply.objects.filter(**{**base, 'date__gte': start, 'date__lte': end})
    if hos_qs.exists():
        hos_agg = hos_qs.aggregate(avg_hrs=Avg('hours_supplied'), total_records=Count('id'))
        hos_result = {
            'avg_daily_hours': round(float(hos_agg['avg_hrs'] or 0), 2),
            'records_count': hos_agg['total_records'],
            'source': 'daily_hos_table',
        }
    else:
        hos_result = _hos_from_hourly_load(base, start, end)

    # ── Energy delivered: FeederEnergyDaily first, fall back to EnergyDelivered ──
    energy_qs = FeederEnergyDaily.objects.filter(**{**base, 'date__gte': start, 'date__lte': end})
    if energy_qs.exists():
        energy_agg = energy_qs.aggregate(total_mwh=Sum('energy_mwh'), days_with_data=Count('id'))
        total_mwh = float(energy_agg['total_mwh'] or 0)
        energy_result = {
            'total_mwh': round(total_mwh, 2),
            'total_gwh': round(total_mwh / 1000, 3),
            'days_with_data': energy_agg['days_with_data'],
            'source': 'feeder_energy_daily',
        }
    else:
        # Fall back to EnergyDelivered (calculated from meter readings)
        ed_qs = EnergyDelivered.objects.filter(**{**base, 'date__gte': start, 'date__lte': end})
        ed_agg = ed_qs.aggregate(total_mwh=Sum('energy_mwh'), days=Count('id'))
        total_mwh = float(ed_agg['total_mwh'] or 0)
        energy_result = {
            'total_mwh': round(total_mwh, 2),
            'total_gwh': round(total_mwh / 1000, 3),
            'days_with_data': ed_agg['days'],
            'source': 'energy_delivered_table' if ed_agg['days'] else 'no_data',
        }

    # ── Interruptions ──
    interruptions_qs = FeederInterruption.objects.filter(
        **base,
        occurred_at__date__gte=start,
        occurred_at__date__lte=end,
    )
    metrics = calculate_interruption_metrics(interruptions_qs)

    # ── Feeder count ──
    from common.models import Feeder
    feeder_count = Feeder.objects.filter(**base).count() if base else Feeder.objects.filter(is_onboarded=True).count()

    return {
        'period': {'start': start_date, 'end': end_date},
        'scope': {'feeder': feeder, 'district': district, 'state': state, 'voltage_level': voltage_level, 'band': band},
        'feeders_in_scope': feeder_count,
        'hours_of_supply': hos_result,
        'energy_delivered': energy_result,
        'interruptions': {
            'total': metrics['total_interruptions'],
            'load_shedding_count': metrics['load_shedding_count'],
            'load_shedding_hours': metrics['load_shedding_hours'],
            'disco_fault_count': metrics['fault_count'],
            'disco_fault_hours': metrics['fault_hours'],
            'avg_turnaround_hours': metrics['avg_turnaround_time'],
            'unresolved': metrics['unresolved_count'],
            'top_fault_types': _named_breakdown(metrics['breakdown_by_type']),
        },
    }


def query_feeder_ranking(start_date: str, end_date: str, metric: str = 'hours_of_supply', limit: int = 10, district: str = None, state: str = None, voltage_level: str = None, band: str = None) -> dict:
    """Rank feeders by hours_of_supply or energy_delivered. metric: 'hours_of_supply' | 'energy_delivered'."""
    from technical.models import DailyHoursOfSupply, FeederEnergyDaily

    start, end = _parse(start_date), _parse(end_date)
    base = _feeder_filter(district=district, state=state, voltage_level=voltage_level, band=band)

    if metric == 'energy_delivered':
        rows = (
            FeederEnergyDaily.objects
            .filter(**{**base, 'date__gte': start, 'date__lte': end})
            .values('feeder__name', 'feeder__business_district__name')
            .annotate(value=Sum('energy_mwh'))
            .order_by('-value')[:limit]
        )
        label = 'total_energy_mwh'
    else:
        rows = (
            DailyHoursOfSupply.objects
            .filter(**{**base, 'date__gte': start, 'date__lte': end})
            .values('feeder__name', 'feeder__business_district__name')
            .annotate(value=Avg('hours_supplied'))
            .order_by('-value')[:limit]
        )
        label = 'avg_hours_of_supply'

    return {
        'period': {'start': start_date, 'end': end_date},
        'metric': metric,
        f'top_{limit}_feeders': [
            {
                'feeder': r['feeder__name'],
                'district': r['feeder__business_district__name'],
                label: round(float(r['value'] or 0), 2),
            }
            for r in rows
        ],
    }


_BAND_THRESHOLDS = {'A': 20, 'B': 16, 'C': 12, 'D': 8, 'E': 0}


def query_band_compliance(band: str, date: str = None) -> dict:
    """Check which feeders in a given service band met their minimum hours-of-supply threshold for a date."""
    from common.models import Band, Feeder
    from technical.models import DailyHoursOfSupply

    band_upper = band.strip().upper()
    threshold = _BAND_THRESHOLDS.get(band_upper)
    if threshold is None:
        return {'error': f'Unknown band "{band}". Valid bands: A, B, C, D, E.'}

    band_obj = Band.objects.filter(name__iexact=band_upper).first()
    if not band_obj:
        return {'error': f'Band {band_upper} not found in the database.'}

    target = _parse(date) if date else (timezone.localdate() - timedelta(days=1))

    feeders = list(Feeder.objects.filter(band=band_obj, is_onboarded=True).select_related('business_district'))
    if not feeders:
        return {'band': band_upper, 'date': str(target), 'message': f'No onboarded feeders found in Band {band_upper}.'}

    feeder_ids = [f.id for f in feeders]

    # Primary: DailyHoursOfSupply
    hos_map = {
        r['feeder_id']: float(r['hours_supplied'])
        for r in DailyHoursOfSupply.objects.filter(feeder_id__in=feeder_ids, date=target).values('feeder_id', 'hours_supplied')
    }

    # Fallback: derive from HourlyLoad (real MW readings — hours where load_mw > 0)
    missing_ids = [f.id for f in feeders if f.id not in hos_map]
    hourly_hos_map: dict = {}
    if missing_ids:
        from technical.models import HourlyLoad
        for r in (
            HourlyLoad.objects.filter(feeder_id__in=missing_ids, date=target, load_mw__gt=0)
            .values('feeder_id')
            .annotate(supply_hours=Count('hour'))
        ):
            hourly_hos_map[r['feeder_id']] = r['supply_hours']

    compliant = []
    non_compliant = []
    no_data = []

    for f in feeders:
        entry = {
            'feeder': f.name,
            'district': f.business_district.name if f.business_district else None,
        }
        if f.id in hos_map:
            hrs = hos_map[f.id]
            entry['hours_of_supply'] = hrs
            entry['data_source'] = 'daily_hos_table'
            entry['shortfall_hrs'] = max(0, round(threshold - hrs, 2))
            if hrs >= threshold:
                compliant.append(entry)
            else:
                non_compliant.append(entry)
        elif f.id in hourly_hos_map:
            hrs = float(hourly_hos_map[f.id])
            entry['hours_of_supply'] = hrs
            entry['data_source'] = 'hourly_load_readings'
            entry['shortfall_hrs'] = max(0, round(threshold - hrs, 2))
            if hrs >= threshold:
                compliant.append(entry)
            else:
                non_compliant.append(entry)
        else:
            entry['hours_of_supply'] = None
            entry['data_source'] = 'no_data'
            no_data.append(entry)

    return {
        'band': band_upper,
        'date': str(target),
        'threshold_hrs': threshold,
        'total_feeders': len(feeders),
        'compliant_count': len(compliant),
        'non_compliant_count': len(non_compliant),
        'no_hos_data_count': len(no_data),
        'compliant_feeders': compliant,
        'non_compliant_feeders': non_compliant,
        'feeders_with_no_hos_data': no_data,
    }


def query_feeder_records(feeder: str) -> dict:
    """Return all-time highest and lowest recorded values for a feeder: hours of supply, energy delivered, and peak load."""
    from common.models import Feeder
    from technical.models import DailyHoursOfSupply, FeederEnergyDaily, HourlyLoad

    obj = Feeder.objects.filter(Q(slug=feeder) | Q(name__icontains=feeder)).first()
    if not obj:
        return {'error': f'Feeder "{feeder}" not found.'}

    hos = DailyHoursOfSupply.objects.filter(feeder=obj).aggregate(
        max_hrs=Max('hours_supplied'),
        min_hrs=Min('hours_supplied'),
        avg_hrs=Avg('hours_supplied'),
        total_days=Count('id'),
    )
    hos_max_day = (
        DailyHoursOfSupply.objects.filter(feeder=obj, hours_supplied=hos['max_hrs']).first()
    )
    hos_min_day = (
        DailyHoursOfSupply.objects.filter(feeder=obj, hours_supplied=hos['min_hrs']).first()
    )

    energy = FeederEnergyDaily.objects.filter(feeder=obj).aggregate(
        max_mwh=Max('energy_mwh'),
        min_mwh=Min('energy_mwh'),
        avg_mwh=Avg('energy_mwh'),
        total_days=Count('id'),
    )
    energy_max_day = (
        FeederEnergyDaily.objects.filter(feeder=obj, energy_mwh=energy['max_mwh']).first()
    )
    energy_min_day = (
        FeederEnergyDaily.objects.filter(feeder=obj, energy_mwh=energy['min_mwh']).first()
    )

    load = HourlyLoad.objects.filter(feeder=obj).aggregate(
        peak_mw=Max('load_mw'),
        min_mw=Min('load_mw'),
        avg_mw=Avg('load_mw'),
        total_readings=Count('id'),
    )
    load_peak_record = (
        HourlyLoad.objects.filter(feeder=obj, load_mw=load['peak_mw']).first()
    )
    load_min_record = (
        HourlyLoad.objects.filter(feeder=obj, load_mw=load['min_mw']).first()
    )

    return {
        'feeder': obj.name,
        'district': obj.business_district.name if obj.business_district else None,
        'all_time_hours_of_supply': {
            'highest_hrs': float(hos['max_hrs'] or 0),
            'highest_on': str(hos_max_day.date) if hos_max_day else None,
            'lowest_hrs': float(hos['min_hrs'] or 0),
            'lowest_on': str(hos_min_day.date) if hos_min_day else None,
            'average_hrs': round(float(hos['avg_hrs'] or 0), 2),
            'days_with_data': hos['total_days'],
        },
        'all_time_energy_delivered': {
            'highest_mwh': float(energy['max_mwh'] or 0),
            'highest_on': str(energy_max_day.date) if energy_max_day else None,
            'lowest_mwh': float(energy['min_mwh'] or 0),
            'lowest_on': str(energy_min_day.date) if energy_min_day else None,
            'average_mwh': round(float(energy['avg_mwh'] or 0), 2),
            'days_with_data': energy['total_days'],
        },
        'all_time_load': {
            'peak_mw': float(load['peak_mw'] or 0),
            'peak_recorded_on': (
                f"{load_peak_record.date} hour {load_peak_record.hour}:00"
                if load_peak_record else None
            ),
            'lowest_mw': float(load['min_mw'] or 0),
            'lowest_recorded_on': (
                f"{load_min_record.date} hour {load_min_record.hour}:00"
                if load_min_record else None
            ),
            'average_mw': round(float(load['avg_mw'] or 0), 2),
            'total_hourly_readings': load['total_readings'],
        },
    }


def query_hourly_load(feeder: str, date: str = None, last_hours: int = None) -> dict:
    """Return hourly load (MW) readings for a feeder. Use date for a specific day or last_hours for recent readings."""
    from common.models import Feeder
    from technical.models import HourlyLoad

    obj = Feeder.objects.filter(Q(slug=feeder) | Q(name__icontains=feeder)).first()
    if not obj:
        return {'error': f'Feeder "{feeder}" not found.'}

    today = timezone.localdate()

    if last_hours:
        cutoff = timezone.now() - timedelta(hours=last_hours)
        qs = HourlyLoad.objects.filter(
            feeder=obj,
            date__gte=cutoff.date(),
        ).order_by('-date', '-hour')
        period_label = f'last {last_hours} hours'
    elif date:
        target = _parse(date)
        qs = HourlyLoad.objects.filter(feeder=obj, date=target).order_by('hour')
        period_label = date
    else:
        qs = HourlyLoad.objects.filter(feeder=obj, date=today).order_by('hour')
        period_label = str(today)

    readings = list(qs.values('date', 'hour', 'load_mw'))

    agg = qs.aggregate(peak=Max('load_mw'), avg=Avg('load_mw'), low=Min('load_mw'))

    return {
        'feeder': obj.name,
        'district': obj.business_district.name if obj.business_district else None,
        'period': period_label,
        'summary': {
            'peak_mw': float(agg['peak'] or 0),
            'average_mw': round(float(agg['avg'] or 0), 2),
            'lowest_mw': float(agg['low'] or 0),
            'readings_count': len(readings),
        },
        'hourly_readings': [
            {
                'date': str(r['date']),
                'hour': f"{r['hour']:02d}:00",
                'load_mw': float(r['load_mw']),
            }
            for r in readings
        ],
    }


def query_system_load(start_date: str, end_date: str, voltage_level: str = None, district: str = None, state: str = None, band: str = None) -> dict:
    """Return total and average load (MW) aggregated across all feeders for a period, with optional voltage/district/band scoping."""
    from technical.models import HourlyLoad

    start, end = _parse(start_date), _parse(end_date)
    base = _feeder_filter(district=district, state=state, voltage_level=voltage_level, band=band)

    # Remap feeder__ keys to direct feeder__ for HourlyLoad
    hl_filter = {k: v for k, v in base.items()}
    hl_qs = HourlyLoad.objects.filter(
        **hl_filter,
        date__gte=start,
        date__lte=end,
    )

    agg = hl_qs.aggregate(
        total_mwh=Sum('load_mw'),
        peak_mw=Max('load_mw'),
        avg_mw=Avg('load_mw'),
        reading_count=Count('id'),
    )

    # Breakdown by voltage level
    breakdown_by_voltage = {}
    for vl in ('11kv', '33kv'):
        vl_agg = hl_qs.filter(feeder__voltage_level=vl).aggregate(
            total=Sum('load_mw'), peak=Max('load_mw'), avg=Avg('load_mw'), count=Count('id')
        )
        if vl_agg['count']:
            breakdown_by_voltage[vl] = {
                'total_mw_sum': round(float(vl_agg['total'] or 0), 2),
                'peak_mw': round(float(vl_agg['peak'] or 0), 2),
                'avg_mw': round(float(vl_agg['avg'] or 0), 2),
                'readings': vl_agg['count'],
            }

    # Daily totals for trend view
    daily_rows = (
        hl_qs
        .values('date')
        .annotate(daily_sum_mw=Sum('load_mw'), daily_peak_mw=Max('load_mw'), daily_avg_mw=Avg('load_mw'))
        .order_by('date')
    )

    return {
        'period': {'start': start_date, 'end': end_date},
        'scope': {'voltage_level': voltage_level, 'district': district, 'state': state, 'band': band},
        'summary': {
            'total_mw_sum': round(float(agg['total_mwh'] or 0), 2),
            'peak_mw': round(float(agg['peak_mw'] or 0), 2),
            'avg_mw': round(float(agg['avg_mw'] or 0), 2),
            'hourly_readings_count': agg['reading_count'],
        },
        'by_voltage_level': breakdown_by_voltage,
        'daily_trend': [
            {
                'date': str(r['date']),
                'total_mw': round(float(r['daily_sum_mw'] or 0), 2),
                'peak_mw': round(float(r['daily_peak_mw'] or 0), 2),
                'avg_mw': round(float(r['daily_avg_mw'] or 0), 2),
            }
            for r in daily_rows
        ],
    }


def query_period_comparison(period1_start: str, period1_end: str, period2_start: str, period2_end: str,
                             feeder: str = None, district: str = None, state: str = None,
                             voltage_level: str = None, band: str = None) -> dict:
    """Compare technical metrics (HOS, energy, load, interruptions) between two date periods."""
    from technical.models import DailyHoursOfSupply, FeederEnergyDaily, FeederInterruption, HourlyLoad, calculate_interruption_metrics

    base = _feeder_filter(feeder, district, state, voltage_level, band)
    p1s, p1e = _parse(period1_start), _parse(period1_end)
    p2s, p2e = _parse(period2_start), _parse(period2_end)

    def _get_metrics(start, end):
        hos = DailyHoursOfSupply.objects.filter(**base, date__gte=start, date__lte=end).aggregate(
            avg=Avg('hours_supplied'), count=Count('id')
        )
        energy = FeederEnergyDaily.objects.filter(**base, date__gte=start, date__lte=end).aggregate(
            total_mwh=Sum('energy_mwh'), days=Count('id')
        )
        load = HourlyLoad.objects.filter(**base, date__gte=start, date__lte=end).aggregate(
            peak=Max('load_mw'), avg=Avg('load_mw'), count=Count('id')
        )
        intr_qs = FeederInterruption.objects.filter(
            **base, occurred_at__date__gte=start, occurred_at__date__lte=end
        )
        intr = calculate_interruption_metrics(intr_qs)
        return {
            'avg_hours_of_supply': round(float(hos['avg'] or 0), 2),
            'hos_records': hos['count'],
            'total_energy_mwh': round(float(energy['total_mwh'] or 0), 2),
            'total_energy_gwh': round(float(energy['total_mwh'] or 0) / 1000, 3),
            'peak_load_mw': round(float(load['peak'] or 0), 2),
            'avg_load_mw': round(float(load['avg'] or 0), 2),
            'total_interruptions': intr['total_interruptions'],
            'load_shedding_hours': intr['load_shedding_hours'],
            'disco_fault_hours': intr['fault_hours'],
            'avg_turnaround_hrs': intr['avg_turnaround_time'],
        }

    def _pct_change(a, b):
        if a == 0:
            return None
        return round((b - a) / a * 100, 1)

    p1 = _get_metrics(p1s, p1e)
    p2 = _get_metrics(p2s, p2e)

    changes = {k: _pct_change(p1[k], p2[k]) for k in p1 if isinstance(p1[k], (int, float))}

    return {
        'scope': {'feeder': feeder, 'district': district, 'state': state, 'voltage_level': voltage_level, 'band': band},
        'period_1': {'start': period1_start, 'end': period1_end, 'metrics': p1},
        'period_2': {'start': period2_start, 'end': period2_end, 'metrics': p2},
        'change_pct': changes,
    }
