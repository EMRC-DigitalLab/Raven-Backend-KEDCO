from datetime import datetime, timedelta

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.utils import timezone


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def _feeder_filter(feeder: str = None, district: str = None, state: str = None) -> dict:
    from common.models import BusinessDistrict, Feeder, State

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
    return f


def query_technical(start_date: str, end_date: str, feeder: str = None, district: str = None, state: str = None) -> dict:
    """Return technical metrics: hours of supply, energy delivered, interruptions."""
    from technical.models import (
        DailyHoursOfSupply,
        FeederEnergyDaily,
        FeederInterruption,
        calculate_interruption_metrics,
    )

    start, end = _parse(start_date), _parse(end_date)
    base = _feeder_filter(feeder, district, state)

    # Hours of supply
    hos = DailyHoursOfSupply.objects.filter(
        **{**base, 'date__gte': start, 'date__lte': end}
    ).aggregate(avg_hrs=Avg('hours_supplied'), total_records=Count('id'))

    # Energy delivered
    energy = FeederEnergyDaily.objects.filter(
        **{**base, 'date__gte': start, 'date__lte': end}
    ).aggregate(total_mwh=Sum('energy_mwh'), days_with_data=Count('id'))

    # Interruptions
    interruptions_qs = FeederInterruption.objects.filter(
        **{k.replace('feeder', 'feeder', 1): v for k, v in base.items()},
        occurred_at__date__gte=start,
        occurred_at__date__lte=end,
    )
    metrics = calculate_interruption_metrics(interruptions_qs)

    # Feeder count in scope
    from common.models import Feeder
    feeder_count = Feeder.objects.filter(**base).count() if base else Feeder.objects.filter(is_onboarded=True).count()

    return {
        'period': {'start': start_date, 'end': end_date},
        'scope': {'feeder': feeder, 'district': district, 'state': state},
        'feeders_in_scope': feeder_count,
        'hours_of_supply': {
            'avg_daily_hours': round(float(hos['avg_hrs'] or 0), 2),
            'records_count': hos['total_records'],
        },
        'energy_delivered': {
            'total_mwh': round(float(energy['total_mwh'] or 0), 2),
            'total_gwh': round(float(energy['total_mwh'] or 0) / 1000, 3),
            'days_with_data': energy['days_with_data'],
        },
        'interruptions': {
            'total': metrics['total_interruptions'],
            'load_shedding_count': metrics['load_shedding_count'],
            'load_shedding_hours': metrics['load_shedding_hours'],
            'disco_fault_count': metrics['fault_count'],
            'disco_fault_hours': metrics['fault_hours'],
            'avg_turnaround_hours': metrics['avg_turnaround_time'],
            'unresolved': metrics['unresolved_count'],
            'top_fault_types': dict(list(
                sorted(metrics['breakdown_by_type'].items(), key=lambda x: x[1]['count'], reverse=True)[:5]
            )),
        },
    }


def query_feeder_ranking(start_date: str, end_date: str, metric: str = 'hours_of_supply', limit: int = 10, district: str = None, state: str = None) -> dict:
    """Rank feeders by hours_of_supply or energy_delivered. metric: 'hours_of_supply' | 'energy_delivered'."""
    from technical.models import DailyHoursOfSupply, FeederEnergyDaily
    from common.models import BusinessDistrict, Feeder, State

    start, end = _parse(start_date), _parse(end_date)

    base: dict = {}
    if district:
        obj = BusinessDistrict.objects.filter(Q(slug=district) | Q(name__icontains=district)).first()
        if obj:
            base['feeder__business_district'] = obj
    if state and not base:
        obj = State.objects.filter(Q(slug=state) | Q(name__icontains=state)).first()
        if obj:
            districts = BusinessDistrict.objects.filter(state=obj)
            base['feeder__business_district__in'] = districts

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
    from technical.models import DailyHoursOfSupply, FeederInterruption

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
    hos_map = {
        r['feeder_id']: float(r['hours_supplied'])
        for r in DailyHoursOfSupply.objects.filter(feeder_id__in=feeder_ids, date=target).values('feeder_id', 'hours_supplied')
    }

    # Interruption hours per feeder (for inference when HOS missing)
    interruption_hours_map: dict = {}
    if len(hos_map) < len(feeders):
        for r in (
            FeederInterruption.objects.filter(feeder_id__in=feeder_ids, occurred_at__date=target)
            .values('feeder_id', 'occurred_at', 'restored_at')
        ):
            fid = r['feeder_id']
            occurred = r['occurred_at']
            restored = r['restored_at']
            if occurred and restored:
                hrs = (restored - occurred).total_seconds() / 3600
            elif occurred:
                hrs = (timezone.now() - occurred).total_seconds() / 3600
            else:
                hrs = 0
            interruption_hours_map[fid] = interruption_hours_map.get(fid, 0) + hrs

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
            entry['shortfall_hrs'] = max(0, round(threshold - hrs, 2))
            if hrs >= threshold:
                compliant.append(entry)
            else:
                non_compliant.append(entry)
        else:
            intr_hrs = interruption_hours_map.get(f.id, 0)
            entry['hours_of_supply'] = None
            entry['inferred_interruption_hours'] = round(intr_hrs, 2)
            if intr_hrs > 0:
                inferred_supply = max(0, 24 - intr_hrs)
                entry['inferred_supply_hrs'] = round(inferred_supply, 2)
                entry['inferred_compliant'] = inferred_supply >= threshold
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
