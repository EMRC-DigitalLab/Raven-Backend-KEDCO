from datetime import datetime

from django.db.models import Avg, Count, Q, Sum


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
