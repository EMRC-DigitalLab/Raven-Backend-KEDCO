from datetime import datetime

from django.db.models import Avg, Count, Q, Sum


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def query_energy_account(start_date: str, end_date: str, station: str = None) -> dict:
    """Return energy account metrics: grid readings, stream A/B, metering gap, NBET billing."""
    from energy_account.models import EAMonthlyReturn, NBETMarketBilateral

    start, end = _parse(start_date), _parse(end_date)

    ea_filter: dict = {}
    try:
        # EAMonthlyReturn has a 'month' or 'period' field — query dynamically
        from energy_account.models import EAMonthlyReturn
        field_names = [f.name for f in EAMonthlyReturn._meta.get_fields()]
        date_field = 'month' if 'month' in field_names else 'period'
        ea_filter = {f'{date_field}__gte': start, f'{date_field}__lte': end}
        if station:
            from common.models import InjectionSubstation
            obj = InjectionSubstation.objects.filter(
                Q(slug=station) | Q(name__icontains=station)
            ).first()
            if obj and 'station' in field_names:
                ea_filter['station'] = obj

        returns = EAMonthlyReturn.objects.filter(**ea_filter)
        count = returns.count()

        # Try to aggregate known EA fields
        numeric_fields = [
            f.name for f in EAMonthlyReturn._meta.get_fields()
            if hasattr(f, 'get_internal_type') and f.get_internal_type() in ('DecimalField', 'FloatField')
        ]
        agg_kwargs = {f: Sum(f) for f in numeric_fields[:12]}
        agg = returns.aggregate(**agg_kwargs) if agg_kwargs else {}

        ea_summary = {k: round(float(v), 2) for k, v in agg.items() if v is not None}

    except Exception as e:
        ea_summary = {'error': str(e)}
        count = 0

    # NBET rates in period
    nbet_rates = list(
        NBETMarketBilateral.objects.filter(
            effective_date__gte=start, effective_date__lte=end, is_active=True
        ).values('effective_date', 'nbet_rate', 'market_operator_rate', 'bilateral')
        .order_by('-effective_date')[:6]
    )

    return {
        'period': {'start': start_date, 'end': end_date},
        'scope': {'station': station},
        'monthly_returns_count': count,
        'energy_account_aggregates': ea_summary,
        'nbet_rates_in_period': [
            {
                'effective_date': str(r['effective_date']),
                'nbet_rate': float(r['nbet_rate'] or 0),
                'mo_rate': float(r['market_operator_rate'] or 0),
                'bilateral': float(r['bilateral'] or 0),
            }
            for r in nbet_rates
        ],
    }


def query_grid_meters(station: str = None, active_only: bool = True) -> dict:
    """Return grid meter registry."""
    from energy_account.models import EAGridMeter

    meter_filter: dict = {}
    if active_only:
        meter_filter['status'] = 'active'
    if station:
        from common.models import InjectionSubstation
        obj = InjectionSubstation.objects.filter(
            Q(slug=station) | Q(name__icontains=station)
        ).first()
        if obj:
            meter_filter['transformer__injectionsubstation'] = obj

    meters = EAGridMeter.objects.filter(**meter_filter)
    by_type = list(meters.values('meter_owner_type').annotate(count=Count('id')))

    return {
        'total_meters': meters.count(),
        'by_type': {r['meter_owner_type']: r['count'] for r in by_type},
        'active_only': active_only,
    }
