from datetime import datetime, date


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def query_tmo_daily_allocation(start_date: str, end_date: str) -> dict:
    """Daily Energy Allocation: actual vs target GWh per day, for the TMO network total."""
    from tmo.services import TMOService

    svc = TMOService(_parse(start_date), _parse(end_date))
    return svc.get_daily_energy()


def query_tmo_segment_breakdown(start_date: str, end_date: str) -> dict:
    """Energy delivered split by segment (MDI / MDNI / Regions) for a period, with each segment's share."""
    from tmo.services import TMOService

    svc = TMOService(_parse(start_date), _parse(end_date))
    return svc.get_energy_by_segment()


def query_tmo_pear(as_of_date: str = None) -> dict:
    """
    PEAR (Premium Energy Allocation Ratio): MD (MDI+MDNI) vs Non-MD share of energy,
    for yesterday and month-to-date, against the configured target mix.
    """
    from tmo.services import TMOService

    day = _parse(as_of_date) if as_of_date else date.today()
    svc = TMOService(day, day)
    return svc.get_pear()


def query_tmo_voltage_breakdown(start_date: str, end_date: str) -> dict:
    """Energy delivered split by voltage level (33kV vs 11kV) per segment, for a period."""
    from tmo.services import TMOService

    svc = TMOService(_parse(start_date), _parse(end_date))
    return svc.get_energy_by_voltage()


def query_tmo_overview(start_date: str, end_date: str) -> dict:
    """TMO Technical Dashboard overview: total feeders, target vs actual GWh, and band-compliance summary."""
    from tmo.services import TMOService

    svc = TMOService(_parse(start_date), _parse(end_date))
    return svc.get_overview()


def query_tmo_feeder_composition(feeder: str, start_date: str, end_date: str) -> dict:
    """
    Explain exactly how a specific 33kV feeder's daily energy figure is built: its own raw
    meter reading, every downstream child feeder subtracted from it (with each child's own
    value), why a child is or isn't subtracted, and the resulting net total per day. Use this
    whenever asked "how was X's number made up", "which feeders make up X", or "what's the
    difference between X's raw reading and its reported total".
    """
    from django.db.models import Q
    from common.models import Feeder
    from tmo.services import (
        TMOService, _per_feeder_daily_map,
        PARENT_NEVER_SUBTRACTS_SLUGS, CHILD_NEVER_SUBTRACTED_SLUGS,
        CHILD_ZERO_ENERGY_SLUGS, PARENT_UNCLASSIFIED_BY_TCN_SLUGS,
    )

    obj = Feeder.objects.filter(Q(slug=feeder) | Q(name__icontains=feeder)).first()
    if not obj:
        return {'error': f'No feeder found matching "{feeder}"'}

    from_date, to_date = _parse(start_date), _parse(end_date)
    svc = TMOService(from_date, to_date)
    feeders_by_id, true_33kv_ids, all_11kv_ids, children_by_parent = svc._segment_topology()

    if obj.id not in true_33kv_ids:
        return {
            'feeder': obj.name,
            'note': (
                'This feeder is not a true 33kV bulk parent in the current topology — it is '
                'either an 11kV retail feeder or a child counted directly under another parent. '
                'It has no children of its own to subtract.'
            ),
        }

    never_subtracts = obj.slug in PARENT_NEVER_SUBTRACTS_SLUGS
    unclassified = obj.slug in PARENT_UNCLASSIFIED_BY_TCN_SLUGS
    children_ids = [] if never_subtracts else children_by_parent.get(obj.id, [])

    all_ids = {obj.id} | set(children_ids)
    per_feeder = _per_feeder_daily_map(all_ids, from_date, to_date)

    children_info = []
    for cid in children_ids:
        cf = feeders_by_id.get(cid)
        if cf is None:
            continue
        exempt_reason = None
        if cf.slug in CHILD_NEVER_SUBTRACTED_SLUGS:
            exempt_reason = 'confirmed never subtracted from any parent — counted separately'
        elif cf.slug in CHILD_ZERO_ENERGY_SLUGS:
            exempt_reason = 'confirmed no real energy value — excluded entirely'
        children_info.append({'name': cf.name, 'slug': cf.slug, 'exempt': exempt_reason})

    days = []
    for d_str in sorted(per_feeder.get(obj.id, {}).keys()):
        gross = per_feeder.get(obj.id, {}).get(d_str, 0.0)
        child_values = []
        children_sum = 0.0
        for cid in children_ids:
            cf = feeders_by_id.get(cid)
            if cf is None or cf.slug in CHILD_NEVER_SUBTRACTED_SLUGS:
                continue
            val = per_feeder.get(cid, {}).get(d_str, 0.0)
            child_values.append({'name': cf.name, 'value_mwh': round(val, 2)})
            children_sum += val
        days.append({
            'date':            d_str,
            'raw_reading_mwh': round(gross, 2),
            'children':        child_values,
            'children_sum_mwh': round(children_sum, 2),
            'net_mwh':         round(gross - children_sum, 2),
        })

    return {
        'feeder': {'name': obj.name, 'slug': obj.slug, 'voltage_level': obj.voltage_level},
        'period': {'from': start_date, 'to': end_date},
        'never_subtracts_children': never_subtracts,
        'unclassified_by_tcn': unclassified,
        'children': children_info,
        'days': days,
        'methodology': (
            'net = raw meter reading minus every listed child\'s own value (not floored at '
            'zero — a genuine negative net is possible and matches how TCN itself reports it). '
            'A child only appears here if this feeder\'s raw reading actually captures its '
            'energy; children marked "exempt" are known, individually-verified exceptions.'
        ),
    }


def query_tmo_bulk_composition(start_date: str, end_date: str, limit: int = 20) -> dict:
    """
    List which feeders make up the TMO Daily Energy Allocation total for a period, ranked by
    contribution. Use this for "which feeders make up this total", "what's driving the
    allocation number", or "top contributors to the network total".
    """
    from tmo.services import TMOService

    svc = TMOService(_parse(start_date), _parse(end_date))
    bulk_ids = svc._bulk_feeder_ids()

    from common.models import Feeder
    from tmo.services import _per_feeder_daily_map
    feeders_by_id = {f.id: f for f in Feeder.objects.filter(id__in=bulk_ids)}
    per_feeder = _per_feeder_daily_map(bulk_ids, _parse(start_date), _parse(end_date))

    totals = []
    grand_total = 0.0
    for fid in bulk_ids:
        total = sum(per_feeder.get(fid, {}).values())
        grand_total += total
        f = feeders_by_id.get(fid)
        if f:
            totals.append({'name': f.name, 'slug': f.slug, 'total_mwh': round(total, 2)})

    totals.sort(key=lambda x: -x['total_mwh'])
    for t in totals:
        t['share_pct'] = round(t['total_mwh'] / grand_total * 100, 1) if grand_total else 0.0

    return {
        'period': {'from': start_date, 'to': end_date},
        'total_feeders_in_population': len(totals),
        'grand_total_mwh': round(grand_total, 2),
        'grand_total_gwh': round(grand_total / 1000, 3),
        'top_contributors': totals[:limit],
    }
