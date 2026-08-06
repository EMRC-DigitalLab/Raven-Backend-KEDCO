# tmo/services.py
import calendar
from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Avg, Count, Max, Sum

from commercial.models import (
    TMOBillingEfficiency,
    TMOCollectionTarget,
    TMOFeederTarget,
)
from commercial.models import CommercialCustomer
from common.models import Band, Feeder, FeederSupplyRelationship
from technical.models import DailyHoursOfSupply, EnergyDelivered, FeederInterruption, HourlyLoad
from technical.utils.energy_utils import (
    DAILY_BALLOON_LIMIT,
    calculate_energy_delivered,
    calculate_energy_delivered_per_feeder,
)
from tmo.constants import STANDARD_DAILY_FORECAST_GWH
from tmo.models import TMODailyAllocation, TMOFeederSupplyTarget, TMOIncident, TMOMonthlySegmentTarget, TMONetworkConfig, TMONetworkDispatch, TMOSupplyHoursTarget


# Confirmed phantom duplicate feeder records (same physical feeder re-onboarded
# under a second substation with no real meter history). Excluded here rather
# than deleted because each has real HourlyLoad/billing/interruption history
# attached that a straight delete would destroy — proper merge is a separate,
# larger data-cleanup effort.
DUPLICATE_FEEDER_SLUGS = ['KN-DAK-RAN', 'KN-DAK-GEZ', 'KN-NAI-DAW']


# ── Helpers ──────────────────────────────────────────────────────────────────


def _classify_feeders(feeder_ids, from_date, to_date):
    """
    Split feeder_ids into (meter_ids, balloon_ids) for the given period.
    Mirrors the logic in calculate_energy_delivered_per_feeder:
      meter  — feeder has readings AND max daily value <= DAILY_BALLOON_LIMIT
      balloon — any day exceeds the limit, or feeder has no readings at all
                → system estimate (avg_load × supply_hours) will be used
    """
    stats = (
        EnergyDelivered.objects
        .filter(feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date)
        .values('feeder_id')
        .annotate(max_daily=Max('energy_mwh'), cnt=Count('id'))
    )
    meter_ids = set()
    for row in stats:
        max_daily = float(row['max_daily'] or 0)
        if int(row['cnt'] or 0) > 0 and 0 < max_daily <= DAILY_BALLOON_LIMIT:
            meter_ids.add(row['feeder_id'])
    balloon_ids = set(feeder_ids) - meter_ids
    return meter_ids, balloon_ids


def _energy_feeder_ids(feeder_ids):
    """
    Remove 33KV feeders that actively supply downstream 11KV feeders.
    Those are upstream/bulk meters — counting them alongside their downstream
    11KV feeders would double-count the same energy.
    Only 33KV feeders with direct customer connections are kept.
    """
    upstream_ids = set(
        FeederSupplyRelationship.objects
        .filter(supplier_feeder_id__in=feeder_ids, status='active')
        .values_list('supplier_feeder_id', flat=True)
        .distinct()
    )
    return [fid for fid in feeder_ids if fid not in upstream_ids]


def _daily_energy_breakdown(feeder_ids, from_date, to_date):
    """
    Returns {date_str: total_mwh} for the set of feeders using the same
    balloon + system-estimate logic as calculate_energy_delivered_per_feeder:
      - meter feeders  → sum actual EnergyDelivered per day
      - balloon feeders → avg_load × supply_hours from HourlyLoad per day
    """
    if not feeder_ids:
        return {}

    meter_ids, balloon_ids = _classify_feeders(feeder_ids, from_date, to_date)
    daily = defaultdict(float)

    if meter_ids:
        for row in (
            EnergyDelivered.objects
            .filter(feeder_id__in=meter_ids, date__gte=from_date, date__lte=to_date)
            .values('date').annotate(total=Sum('energy_mwh'))
        ):
            daily[str(row['date'])] += float(row['total'] or 0)

    if balloon_ids:
        for row in (
            HourlyLoad.objects
            .filter(feeder_id__in=balloon_ids, date__gte=from_date, date__lte=to_date, load_mw__gt=0)
            .values('feeder_id', 'date')
            .annotate(avg_load=Avg('load_mw'), supply_hours=Count('hour'))
        ):
            daily[str(row['date'])] += float(row['avg_load'] or 0) * int(row['supply_hours'] or 0)

    return dict(daily)


def _feeder_energy_by_day(feeder_id, from_date, to_date):
    """
    Returns {date_str: mwh} for a single feeder with balloon+system logic.
    """
    stats = EnergyDelivered.objects.filter(
        feeder_id=feeder_id, date__gte=from_date, date__lte=to_date
    ).aggregate(max_daily=Max('energy_mwh'), cnt=Count('id'))

    use_meter = (
        int(stats['cnt'] or 0) > 0 and
        float(stats['max_daily'] or 0) > 0 and
        float(stats['max_daily'] or 0) <= DAILY_BALLOON_LIMIT
    )

    if use_meter:
        return {
            str(r.date): float(r.energy_mwh)
            for r in EnergyDelivered.objects.filter(
                feeder_id=feeder_id, date__gte=from_date, date__lte=to_date
            )
        }

    return {
        str(row['date']): float(row['avg_load'] or 0) * int(row['supply_hours'] or 0)
        for row in (
            HourlyLoad.objects
            .filter(feeder_id=feeder_id, date__gte=from_date, date__lte=to_date, load_mw__gt=0)
            .values('date').annotate(avg_load=Avg('load_mw'), supply_hours=Count('hour'))
        )
    }

def _pct(numerator, denominator):
    try:
        return round(float(numerator) / float(denominator) * 100, 2)
    except (ZeroDivisionError, TypeError, ValueError):
        return 0.0


def _var_pct(actual, target):
    try:
        return round((float(actual) - float(target)) / float(target) * 100, 2)
    except (ZeroDivisionError, TypeError, ValueError):
        return 0.0


def _compliance(pct):
    if pct > 105:
        return 'exceeding'
    if pct >= 95:
        return 'on_target'
    if pct >= 85:
        return 'below_target'
    if pct >= 75:
        return 'poor'
    return 'critical'


def resolve_date_params(request):
    """
    Parse query params → (from_date, to_date).
    Priority: from_date+to_date > month > date > T-1 (yesterday).
    """
    p = request.query_params

    if p.get('from_date') and p.get('to_date'):
        return date.fromisoformat(p['from_date']), date.fromisoformat(p['to_date'])

    if p.get('month'):
        year, month = map(int, p['month'].split('-'))
        from_date = date(year, month, 1)
        if month == 12:
            to_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            to_date = date(year, month + 1, 1) - timedelta(days=1)
        return from_date, to_date

    if p.get('date'):
        d = date.fromisoformat(p['date'])
        return d, d

    yesterday = date.today() - timedelta(days=1)
    return yesterday, yesterday


def _get_segment_feeder_ids():
    """
    MDI / MDNI feeder sets derived from the pl_segment field (authoritative).
    Falls back to CommercialCustomer lookup for feeders without pl_segment set.
    Returns (mdi_ids set, mdni_ids set).
    """
    mdi_ids  = set()
    mdni_ids = set()

    # Primary: use pl_segment field (set by import_feeder_segmentation command)
    for fid, seg in (
        Feeder.objects
        .filter(is_onboarded=True, pl_segment__in=['MDI', 'MDNI'])
        .values_list('id', 'pl_segment')
    ):
        if seg == 'MDI':
            mdi_ids.add(fid)
        else:
            mdni_ids.add(fid)

    # Fallback: any onboarded feeder without pl_segment still gets segmented via
    # CommercialCustomer so legacy data continues to work.
    already_segmented = mdi_ids | mdni_ids
    fallback_mdi = set(
        CommercialCustomer.objects
        .filter(customer_type='MDI')
        .exclude(feeder_id__in=already_segmented)
        .values_list('feeder_id', flat=True)
        .distinct()
    )
    fallback_mdni = set(
        CommercialCustomer.objects
        .filter(customer_type='MDNI')
        .exclude(feeder_id__in=already_segmented | fallback_mdi)
        .values_list('feeder_id', flat=True)
        .distinct()
    )
    mdi_ids  |= fallback_mdi
    mdni_ids |= fallback_mdni

    return mdi_ids, mdni_ids


# ── Service class ─────────────────────────────────────────────────────────────

class TMOService:
    """
    Central service for the TMO live dashboard.
    Always operates over (from_date, to_date); T-1 is the default when
    resolve_date_params finds no explicit param.

    filters = {
        'segment'  : 'MDI' | 'MDNI' | 'MINIGRID',
        'state'    : slug,
        'district' : slug,
        'band'     : slug,
        'voltage'  : '11kv' | '33kv',
        'feeder'   : slug,
    }
    """

    def __init__(self, from_date, to_date, filters=None):
        self.from_date   = from_date
        self.to_date     = to_date
        self.filters     = filters or {}
        self._mdi_ids    = None
        self._mdni_ids   = None
        self._upstream_ids_cache = None

    def _energy_ids(self, feeder_ids):
        """Strip upstream 33KV feeders (those with active downstream 11KV feeders)."""
        return _energy_feeder_ids(feeder_ids)

    # Lazy-load segment IDs once per service instance
    @property
    def mdi_ids(self):
        if self._mdi_ids is None:
            self._mdi_ids, self._mdni_ids = _get_segment_feeder_ids()
        return self._mdi_ids

    @property
    def mdni_ids(self):
        if self._mdni_ids is None:
            self._mdi_ids, self._mdni_ids = _get_segment_feeder_ids()
        return self._mdni_ids

    def _base_feeder_qs(self):
        qs = Feeder.objects.filter(is_onboarded=True).select_related(
            'band', 'substation', 'substation__state', 'business_district'
        )
        f = self.filters
        if f.get('state'):
            qs = qs.filter(substation__state__slug=f['state'])
        if f.get('district'):
            qs = qs.filter(business_district__slug=f['district'])
        if f.get('band'):
            qs = qs.filter(band__slug=f['band'])
        if f.get('voltage'):
            qs = qs.filter(voltage_level=f['voltage'])
        if f.get('feeder'):
            qs = qs.filter(slug=f['feeder'])
        if f.get('segment'):
            seg = f['segment'].upper()
            if seg == 'MDI':
                qs = qs.filter(id__in=self.mdi_ids)
            elif seg == 'MDNI':
                qs = qs.filter(id__in=self.mdni_ids)
            elif seg in ('REGIONS', 'REGIONAL'):
                qs = qs.exclude(id__in=self.mdi_ids).exclude(id__in=self.mdni_ids)
            elif seg == 'MINIGRID':
                qs = qs.filter(is_minigrid=True)
        return qs

    def _segment_label(self, feeder_id):
        if feeder_id in self.mdi_ids:
            return 'MDI'
        if feeder_id in self.mdni_ids:
            return 'MDNI'
        return 'Regional'

    # ── 1. Overview ──────────────────────────────────────────────────────────

    def get_overview(self):
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_ids   = self._energy_ids(feeder_ids)
        total_actual = calculate_energy_delivered(energy_ids, self.from_date, self.to_date)['total_mwh']

        target_agg = TMOFeederTarget.objects.filter(
            feeder_id__in=feeder_ids,
            target_date__gte=self.from_date,
            target_date__lte=self.to_date,
        ).aggregate(total=Sum('target_mwh'))
        total_target = float(target_agg['total'] or 0)

        dispatch_ach = _pct(total_actual, total_target)

        band_a   = Band.objects.filter(slug='a').first()
        min_hrs  = float(band_a.minimum_hours) if band_a else 20.0

        supply_rows = list(
            DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg_hours=Avg('hours_supplied'))
        )
        compliant     = sum(1 for r in supply_rows if float(r['avg_hours'] or 0) >= min_hrs)
        total_tracked = len(supply_rows)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'total_feeders': feeder_qs.count(),
            'energy_dispatch': {
                'target_mwh':       round(total_target, 2),
                'actual_mwh':       round(total_actual, 2),
                'variance_mwh':     round(total_actual - total_target, 2),
                'achievement_pct':  round(dispatch_ach, 1),
                'status':           _compliance(dispatch_ach),
            },
            'supply_compliance': {
                'compliant_feeders': compliant,
                'total_feeders':     total_tracked,
                'compliance_pct':    round(_pct(compliant, total_tracked), 1),
            },
        }

    # ── 2. Feeder Dispatch ───────────────────────────────────────────────────

    def get_feeder_dispatch(self):
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_map = calculate_energy_delivered_per_feeder(self._energy_ids(feeder_ids), self.from_date, self.to_date)
        actuals = {fid: d['mwh'] for fid, d in energy_map.items()}

        targets = {
            row['feeder_id']: float(row['total'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder_id__in=feeder_ids,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('target_mwh'))
        }

        feeders = {f.id: f for f in feeder_qs}
        all_ids = set(feeder_ids) | set(actuals) | set(targets)

        rows = []
        for fid in all_ids:
            feeder = feeders.get(fid)
            actual = actuals.get(fid, 0.0)
            target = targets.get(fid, 0.0)
            ach    = _pct(actual, target)
            rows.append({
                'feeder_id':          str(fid),
                'feeder_name':        feeder.name if feeder else str(fid),
                'feeder_slug':        feeder.slug if feeder else '',
                'segment':            self._segment_label(fid),
                'band':               feeder.band.name if feeder and feeder.band else '',
                'state':              (feeder.substation.state.name
                                      if feeder and feeder.substation and feeder.substation.state else ''),
                'district':           feeder.business_district.name if feeder and feeder.business_district else '',
                'is_minigrid':        feeder.is_minigrid if feeder else False,
                'target_mwh':         round(target, 4),
                'actual_mwh':         round(actual, 4),
                'variance_mwh':       round(actual - target, 4),
                'variance_pct':       _var_pct(actual, target),
                'achievement_pct':    round(ach, 1),
                'status':             _compliance(ach),
            })

        rows.sort(key=lambda r: r['achievement_pct'])

        total_target = sum(r['target_mwh'] for r in rows)
        total_actual = sum(r['actual_mwh'] for r in rows)
        ov_ach = _pct(total_actual, total_target)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': rows,
            'summary': {
                'total_target_mwh':        round(total_target, 2),
                'total_actual_mwh':        round(total_actual, 2),
                'variance_mwh':            round(total_actual - total_target, 2),
                'overall_achievement_pct': round(ov_ach, 1),
                'overall_status':          _compliance(ov_ach),
            },
        }

    # ── 3. Energy by Segment ─────────────────────────────────────────────────

    def get_energy_by_segment(self):
        # Clamp to yesterday — today's cross-day diff is really yesterday's
        # energy filed under today's date (see get_daily_energy()), so this
        # total must not include it either or it silently diverges from
        # get_volatility()'s MTD total for the same period.
        to_date = min(self.to_date, date.today() - timedelta(days=1))

        feeder_qs  = self._base_feeder_qs()
        all_ids    = set(feeder_qs.values_list('id', flat=True))

        # Exclude 33KV feeders that supply downstream 11KV feeders — their energy
        # is already captured at the 11KV meter level; including both = double-count
        upstream_33kv = set(
            FeederSupplyRelationship.objects
            .filter(supplier_feeder__voltage_level='33kv', status='active')
            .values_list('supplier_feeder_id', flat=True)
        )
        feeder_ids = all_ids - upstream_33kv

        mdi_ids    = self.mdi_ids  & feeder_ids
        mdni_ids   = self.mdni_ids & feeder_ids
        # Regions = every feeder that is neither MDI nor MDNI (includes minigrids)
        region_ids = feeder_ids - mdi_ids - mdni_ids

        buckets = {
            'MDI':     mdi_ids,
            'MDNI':    mdni_ids,
            'Regions': region_ids,
        }

        def _energy(ids):
            if not ids:
                return 0.0
            return calculate_energy_delivered(self._energy_ids(list(ids)), self.from_date, to_date)['total_mwh']

        def _target(ids):
            if not ids:
                return 0.0
            return float(
                TMOFeederTarget.objects.filter(
                    feeder_id__in=ids,
                    target_date__gte=self.from_date,
                    target_date__lte=to_date,
                ).aggregate(t=Sum('target_mwh'))['t'] or 0
            )

        # Retail-level (11KV/customer) energy per segment. MDI/MDNI classification
        # is only meaningful at this level — a bulk 33KV feeder often serves a mixed
        # customer base and can't be reliably tagged as a single segment (e.g. a
        # whole-district feeder inheriting "MDNI" from one downstream customer).
        # Used ONLY to derive accurate segment SHARE percentages, not absolute totals.
        raw_actual = {name: _energy(ids) for name, ids in buckets.items()}
        raw_total  = sum(raw_actual.values())

        # Bulk network total — same feeder population/method as get_daily_energy(),
        # so this always reconciles with the Daily Energy Allocation slide instead of
        # silently diverging by the technical/distribution loss between bulk 33KV
        # injection and retail 11KV delivery.
        bulk_ids = list(
            self._base_feeder_qs()
            .filter(voltage_level='33kv')
            .exclude(slug__in=DUPLICATE_FEEDER_SLUGS)
            .exclude(is_minigrid=True)
            .values_list('id', flat=True)
        )
        bulk_total = calculate_energy_delivered(bulk_ids, self.from_date, to_date)['total_mwh']

        segments = []
        totals   = {'actual': 0.0, 'target': 0.0}
        for name, ids in buckets.items():
            share  = (raw_actual[name] / raw_total) if raw_total else 0.0
            actual = bulk_total * share
            target = _target(ids)
            ach    = _pct(actual, target)
            totals['actual'] += actual
            totals['target'] += target
            segments.append({
                'segment':         name,
                'feeder_count':    len(ids),
                'target_mwh':      round(target, 2),
                'actual_mwh':      round(actual, 2),
                'actual_gwh':      round(actual / 1000, 4),
                'variance_mwh':    round(actual - target, 2),
                'achievement_pct': round(ach, 1),
                'status':          _compliance(ach),
                'share_pct':       0.0,   # filled after total is known
            })

        # Fill share_pct now that total is known
        total_actual = totals['actual']
        for seg in segments:
            seg['share_pct'] = round(_pct(seg['actual_mwh'], total_actual), 1)

        return {
            'period':          {'from': str(self.from_date), 'to': str(self.to_date)},
            'total_actual_mwh': round(total_actual, 2),
            'total_actual_gwh': round(total_actual / 1000, 4),
            'segments':         segments,
        }

    # ── 4. Supply Compliance ─────────────────────────────────────────────────

    def get_supply_compliance(self):
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        supply_map = {
            row['feeder_id']: row
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(
                avg_hours=Avg('hours_supplied'),
                total_hours=Sum('hours_supplied'),
                days=Count('id'),
            )
        }

        feeders = {f.id: f for f in feeder_qs}

        # Admin-set segment targets for the period's month (use from_date month)
        segment_target_map = {
            row.segment: float(row.target_hours)
            for row in TMOSupplyHoursTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
            )
        }

        # Per-feeder targets — current month first, then most-recent upload as base default
        month_targets = {
            str(row.feeder_id): float(row.target_hours)
            for row in TMOFeederSupplyTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
                feeder_id__in=feeder_ids,
            )
        }
        missing_ids = [fid for fid in feeder_ids if str(fid) not in month_targets]
        base_targets = {}
        if missing_ids:
            # PostgreSQL DISTINCT ON: picks the latest (year DESC, month DESC) row per feeder
            for row in (
                TMOFeederSupplyTarget.objects
                .filter(feeder_id__in=missing_ids)
                .order_by('feeder_id', '-year', '-month')
                .distinct('feeder_id')
                .values('feeder_id', 'target_hours')
            ):
                base_targets[str(row['feeder_id'])] = float(row['target_hours'])
        feeder_target_map = {**base_targets, **month_targets}

        def _dm_status(fid, feeder):
            if fid in self.mdi_ids:
                return 'MDI'
            is_band_a = feeder and feeder.band and feeder.band.slug == 'a'
            is_33kv   = feeder and feeder.voltage_level == '33kv'
            if is_band_a or is_33kv:
                return 'Non-MDI Band A'
            return 'Non-MDI, Non-Band A'

        rows = []
        for fid in feeder_ids:
            feeder  = feeders.get(fid)
            s       = supply_map.get(fid, {})
            avg_h   = float(s.get('avg_hours') or 0)
            dm_seg  = _dm_status(fid, feeder)
            # Priority: per-feeder upload > segment admin target > band minimum
            if str(fid) in feeder_target_map:
                min_h = feeder_target_map[str(fid)]
            elif dm_seg in segment_target_map:
                min_h = segment_target_map[dm_seg]
            else:
                min_h = float(feeder.band.minimum_hours) if feeder and feeder.band else 0.0
            c_pct  = _pct(avg_h, min_h) if min_h else 0.0
            rows.append({
                'feeder_id':          str(fid),
                'feeder_name':        feeder.name if feeder else str(fid),
                'feeder_slug':        feeder.slug if feeder else '',
                'voltage_level':      feeder.voltage_level if feeder else '',
                'segment':            self._segment_label(fid),
                'dm_status':          _dm_status(fid, feeder),
                'band':               feeder.band.name if feeder and feeder.band else '',
                'band_minimum_hours': min_h,
                'avg_daily_hours':    round(avg_h, 2),
                'gap_hours':          round(avg_h - min_h, 2),
                'total_hours':        round(float(s.get('total_hours') or 0), 2),
                'days_recorded':      s.get('days', 0),
                'compliance_pct':     round(c_pct, 1),
                'status':             _compliance(c_pct),
                'bucket':             _compliance(c_pct),   # alias used by frontend RAG coloring
                'is_minigrid':        feeder.is_minigrid if feeder else False,
            })

        # Disambiguate duplicate feeder names by appending the slug
        from collections import Counter
        name_counts = Counter(r['feeder_name'] for r in rows)
        for r in rows:
            if name_counts[r['feeder_name']] > 1 and r['feeder_slug']:
                r['feeder_name'] = f"{r['feeder_name']} ({r['feeder_slug']})"

        rows.sort(key=lambda r: (r['avg_daily_hours'] == 0, -r['compliance_pct'] if r['avg_daily_hours'] == 0 else r['compliance_pct']))
        compliant = sum(1 for r in rows if r['compliance_pct'] >= 100)
        total     = len(rows)

        return {
            'period':  {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': rows,
            'summary': {
                'compliant_feeders':  compliant,
                'total_feeders':      total,
                'compliance_rate_pct': round(_pct(compliant, total), 1),
            },
        }

    # ── 5. Collection ────────────────────────────────────────────────────────

    def get_collection(self):
        qs = TMOCollectionTarget.objects.filter(
            period_month__gte=self.from_date,
            period_month__lte=self.to_date,
        ).order_by('period_month', 'segment_code')

        if self.filters.get('segment'):
            qs = qs.filter(segment_code=self.filters['segment'].upper())

        rows = []
        for obj in qs:
            target = float(obj.target_amount)
            actual = float(obj.actual_amount)
            ach    = _pct(actual, target)
            rows.append({
                'segment_code':    obj.segment_code,
                'sub_segment':     obj.sub_segment,
                'period_month':    str(obj.period_month),
                'target_amount':   round(target, 2),
                'actual_amount':   round(actual, 2),
                'variance':        round(actual - target, 2),
                'achievement_pct': round(ach, 1),
                'status':          _compliance(ach),
            })

        total_target = sum(r['target_amount'] for r in rows)
        total_actual = sum(r['actual_amount'] for r in rows)
        ov_ach       = _pct(total_actual, total_target)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'rows':   rows,
            'summary': {
                'total_target':            round(total_target, 2),
                'total_actual':            round(total_actual, 2),
                'variance':                round(total_actual - total_target, 2),
                'overall_achievement_pct': round(ov_ach, 1),
                'overall_status':          _compliance(ov_ach),
            },
        }

    # ── 6. Billing Efficiency ────────────────────────────────────────────────

    def get_billing_efficiency(self):
        qs = TMOBillingEfficiency.objects.filter(
            period_month__gte=self.from_date,
            period_month__lte=self.to_date,
        ).order_by('scope_type', 'scope_label', 'period_month')

        rows = []
        for obj in qs:
            del_gwh = float(obj.energy_delivered_gwh)
            bil_gwh = float(obj.energy_billed_gwh)
            t_rev   = float(obj.target_revenue_amount)
            b_rev   = float(obj.billed_amount)
            be_pct  = _pct(bil_gwh, del_gwh)
            rr_pct  = _pct(b_rev, t_rev)
            rows.append({
                'scope_type':             obj.scope_type,
                'scope_code':             obj.scope_code,
                'scope_label':            obj.scope_label or obj.scope_code,
                'period_month':           str(obj.period_month),
                'energy_delivered_gwh':   del_gwh,
                'energy_billed_gwh':      bil_gwh,
                'billing_efficiency_pct': round(be_pct, 1),
                'target_revenue':         round(t_rev, 2),
                'billed_amount':          round(b_rev, 2),
                'revenue_efficiency_pct': round(rr_pct, 1),
                'be_status':              _compliance(be_pct),
                'rr_status':              _compliance(rr_pct),
            })

        total_del  = sum(r['energy_delivered_gwh'] for r in rows)
        total_bil  = sum(r['energy_billed_gwh']    for r in rows)
        total_trev = sum(r['target_revenue']        for r in rows)
        total_brev = sum(r['billed_amount']         for r in rows)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'rows':   rows,
            'summary': {
                'total_energy_delivered_gwh': round(total_del, 4),
                'total_energy_billed_gwh':    round(total_bil, 4),
                'overall_billing_eff_pct':    round(_pct(total_bil, total_del), 1),
                'total_target_revenue':       round(total_trev, 2),
                'total_billed_amount':        round(total_brev, 2),
                'overall_revenue_eff_pct':    round(_pct(total_brev, total_trev), 1),
            },
        }

    # ── 7. P&L Segment Targets ───────────────────────────────────────────────

    def get_pnl_targets(self):
        """Energy actuals vs TMOMonthlySegmentTarget for MDI and MDNI."""
        target_qs = TMOMonthlySegmentTarget.objects.filter(
            year=self.from_date.year,
            month=self.from_date.month,
        )
        targets_by_seg = {t.segment: t for t in target_qs}

        def _energy_actual(ids):
            if not ids:
                return 0.0
            return calculate_energy_delivered(self._energy_ids(list(ids)), self.from_date, self.to_date)['total_mwh']

        segments = []
        for seg_name, ids in [('MDI', self.mdi_ids), ('MDNI', self.mdni_ids)]:
            actual = _energy_actual(ids)
            t      = targets_by_seg.get(seg_name)
            t_mwh  = float(t.target_energy_mwh)     if t else 0.0
            t_rev  = float(t.target_revenue_ngn)    if t else 0.0
            t_col  = float(t.target_collection_ngn) if t else 0.0
            ach    = _pct(actual, t_mwh)
            segments.append({
                'segment':               seg_name,
                'feeder_count':          len(ids),
                'target_energy_mwh':     round(t_mwh, 2),
                'actual_energy_mwh':     round(actual, 2),
                'energy_achievement_pct': round(ach, 1),
                'energy_status':         _compliance(ach),
                'target_revenue_ngn':    round(t_rev, 2),
                'target_collection_ngn': round(t_col, 2),
            })

        return {
            'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
            'segments': segments,
        }

    # ── 8. Minigrids ─────────────────────────────────────────────────────────

    def get_minigrids(self):
        feeder_qs  = self._base_feeder_qs().filter(is_minigrid=True)
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_map = calculate_energy_delivered_per_feeder(self._energy_ids(feeder_ids), self.from_date, self.to_date)
        actuals = {fid: d['mwh'] for fid, d in energy_map.items()}
        supply = {
            row['feeder_id']: float(row['avg'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg=Avg('hours_supplied'))
        }
        targets = {
            row['feeder_id']: float(row['total'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder_id__in=feeder_ids,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('target_mwh'))
        }

        rows = []
        for feeder in feeder_qs:
            fid    = feeder.id
            actual = actuals.get(fid, 0.0)
            target = targets.get(fid, 0.0)
            avg_h  = supply.get(fid, 0.0)
            ach    = _pct(actual, target)
            rows.append({
                'feeder_id':       str(fid),
                'feeder_name':     feeder.name,
                'state':           (feeder.substation.state.name
                                   if feeder.substation and feeder.substation.state else ''),
                'target_mwh':      round(target, 4),
                'actual_mwh':      round(actual, 4),
                'variance_mwh':    round(actual - target, 4),
                'achievement_pct': round(ach, 1),
                'avg_daily_hours': round(avg_h, 2),
                'status':          _compliance(ach),
            })

        return {
            'period':    {'from': str(self.from_date), 'to': str(self.to_date)},
            'minigrids': rows,
            'count':     len(rows),
        }

    # ── 9. All Feeders ───────────────────────────────────────────────────────

    def get_feeders(self):
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_map = calculate_energy_delivered_per_feeder(self._energy_ids(feeder_ids), self.from_date, self.to_date)
        actuals = {fid: d['mwh'] for fid, d in energy_map.items()}
        supply = {
            row['feeder_id']: float(row['avg'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg=Avg('hours_supplied'))
        }
        targets = {
            row['feeder_id']: float(row['total'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder_id__in=feeder_ids,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('target_mwh'))
        }

        rows = []
        for feeder in feeder_qs:
            fid    = feeder.id
            actual = actuals.get(fid, 0.0)
            target = targets.get(fid, 0.0)
            avg_h  = supply.get(fid, 0.0)
            min_h  = float(feeder.band.minimum_hours) if feeder.band else 0.0
            ach    = _pct(actual, target)
            h_c    = _pct(avg_h, min_h) if min_h else 0.0
            rows.append({
                'feeder_id':              str(fid),
                'feeder_name':            feeder.name,
                'feeder_slug':            feeder.slug,
                'segment':                self._segment_label(fid),
                'band':                   feeder.band.name if feeder.band else '',
                'voltage_level':          feeder.voltage_level,
                'state':                  (feeder.substation.state.name
                                          if feeder.substation and feeder.substation.state else ''),
                'district':               feeder.business_district.name if feeder.business_district else '',
                'is_minigrid':            feeder.is_minigrid,
                'target_mwh':             round(target, 4),
                'actual_mwh':             round(actual, 4),
                'variance_mwh':           round(actual - target, 4),
                'energy_achievement_pct': round(ach, 1),
                'energy_status':          _compliance(ach),
                'avg_daily_hours':        round(avg_h, 2),
                'band_minimum_hours':     min_h,
                'hours_compliance_pct':   round(h_c, 1),
                'hours_status':           _compliance(h_c),
            })

        rows.sort(key=lambda r: r['energy_achievement_pct'])

        return {
            'period':  {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': rows,
            'count':   len(rows),
        }

    # ── 10. P&L Mix Volatility Index ────────────────────────────────────────

    def get_volatility(self):
        """
        P&L Mix Volatility Index.
        Compares each segment's share of total energy for:
          - the selected day (T-1 by default)
          - month-to-date (start of that month → selected day)
        Flags when high-ROI segment share is declining vs MTD average.
        """
        # Day = to_date (T-1 or user-selected date), clamped to never reach today —
        # today can never have a real, finished number (see get_daily_energy()), so
        # "yesterday" and "month-to-date" must never include it either.
        day = min(self.to_date, date.today() - timedelta(days=1))
        mtd_start = day.replace(day=1)

        feeder_qs  = self._base_feeder_qs()
        feeder_ids = set(feeder_qs.values_list('id', flat=True))

        def _energy(ids, from_d, to_d):
            if not ids:
                return 0.0
            return calculate_energy_delivered(self._energy_ids(list(ids)), from_d, to_d)['total_mwh']

        def _raw_total(from_d, to_d):
            return calculate_energy_delivered(self._energy_ids(list(feeder_ids)), from_d, to_d)['total_mwh']

        # Bulk network total — same feeder population/method as get_daily_energy()
        # and get_energy_by_segment(), so this reconciles with the Daily Energy
        # Allocation / P&L segment slides instead of silently diverging by the
        # technical/distribution loss between bulk 33KV injection and retail
        # 11KV delivery.
        def _bulk_total(from_d, to_d):
            bulk_ids = list(
                self._base_feeder_qs()
                .filter(voltage_level='33kv')
                .exclude(slug__in=DUPLICATE_FEEDER_SLUGS)
                .exclude(is_minigrid=True)
                .values_list('id', flat=True)
            )
            return calculate_energy_delivered(bulk_ids, from_d, to_d)['total_mwh']

        def _share(part, total):
            return round(_pct(part, total), 1) if total else 0.0

        def _remark(seg, diff):
            if seg in ('MDI', 'MDNI'):
                if diff < -1:
                    return 'Decline –'
                if diff > 1:
                    return 'Growth +'
                return 'Stable'
            else:  # Regions (includes minigrids)
                if diff > 1:
                    return 'High – Daily spike is sustained'
                if diff < -1:
                    return 'Declining –'
                return 'Stable'

        # Retail-level (11KV/customer) energy per segment, used ONLY to derive
        # accurate segment SHARE percentages — see get_energy_by_segment().
        # Day — Regions = everything that isn't MDI or MDNI (minigrids included)
        day_raw_total = _raw_total(day, day)
        day_mdi_raw   = _energy(self.mdi_ids & feeder_ids, day, day)
        day_mdni_raw  = _energy(self.mdni_ids & feeder_ids, day, day)
        day_reg_raw   = max(day_raw_total - day_mdi_raw - day_mdni_raw, 0.0)

        day_total = _bulk_total(day, day)
        day_mdi   = day_total * (day_mdi_raw / day_raw_total) if day_raw_total else 0.0
        day_mdni  = day_total * (day_mdni_raw / day_raw_total) if day_raw_total else 0.0
        day_reg   = day_total * (day_reg_raw / day_raw_total) if day_raw_total else 0.0

        # MTD
        mtd_raw_total = _raw_total(mtd_start, day)
        mtd_mdi_raw   = _energy(self.mdi_ids & feeder_ids, mtd_start, day)
        mtd_mdni_raw  = _energy(self.mdni_ids & feeder_ids, mtd_start, day)
        mtd_reg_raw   = max(mtd_raw_total - mtd_mdi_raw - mtd_mdni_raw, 0.0)

        mtd_total = _bulk_total(mtd_start, day)
        mtd_mdi   = mtd_total * (mtd_mdi_raw / mtd_raw_total) if mtd_raw_total else 0.0
        mtd_mdni  = mtd_total * (mtd_mdni_raw / mtd_raw_total) if mtd_raw_total else 0.0
        mtd_reg   = mtd_total * (mtd_reg_raw / mtd_raw_total) if mtd_raw_total else 0.0

        segments = []
        for seg, d_val, m_val in [
            ('MDI',     day_mdi,  mtd_mdi),
            ('MDNI',    day_mdni, mtd_mdni),
            ('Regions', day_reg,  mtd_reg),
        ]:
            d_share = _share(d_val, day_total)
            m_share = _share(m_val, mtd_total)
            diff    = round(d_share - m_share, 1)
            segments.append({
                'segment':          seg,
                'yesterday_share_pct': d_share,
                'mtd_share_pct':    m_share,
                'difference_pct':   diff,
                'remark':           _remark(seg, diff),
            })

        return {
            'day':      str(day),
            'mtd_from': str(mtd_start),
            'day_total_mwh': round(day_total, 2),
            'mtd_total_mwh': round(mtd_total, 2),
            'segments': segments,
        }

    # ── 11. Daily Network Energy (Forecast vs Actual) ────────────────────────

    def get_daily_energy(self):
        """
        Daily total energy across the network for the selected period.
        Compares against daily target derived from monthly GWh target in TMONetworkConfig.
        Covers Slides 2 & 3 (Daily Energy Forecast / Daily Energy Allocation).
        """
        feeder_ids = list(
            self._base_feeder_qs()
            .filter(voltage_level='33kv')
            .exclude(slug__in=DUPLICATE_FEEDER_SLUGS)
            .exclude(is_minigrid=True)
            .values_list('id', flat=True)
        )

        # 33KV-only: no double-count risk from upstream/downstream pairs, skip _energy_ids()
        daily_map = _daily_energy_breakdown(feeder_ids, self.from_date, self.to_date)
        daily = [{'date': date.fromisoformat(d), 'total_mwh': v} for d, v in sorted(daily_map.items())]

        config = TMONetworkConfig.objects.filter(
            year=self.from_date.year,
            month=self.from_date.month,
        ).first()

        monthly_target_gwh = float(config.monthly_energy_target_gwh) if config else 0.0

        days_in_month = calendar.monthrange(self.from_date.year, self.from_date.month)[1]
        flat_daily_target_gwh = monthly_target_gwh / days_in_month if days_in_month else 0.0

        # Per-day targets: admin override from TMODailyAllocation takes priority.
        # Falls back to the standard day-of-month pattern (STANDARD_DAILY_FORECAST_GWH),
        # then to flat monthly ÷ days as the last resort when no monthly target is set.
        alloc_map = {
            str(a.date): float(a.expected_mw) * 24.0 / 1000.0
            for a in TMODailyAllocation.objects.filter(
                date__gte=self.from_date,
                date__lte=self.to_date,
            )
        }

        days = []
        for row in daily:
            # The cumulative-meter diff for "today" is really yesterday's energy
            # (today's reading − yesterday's reading = yesterday's full 24h span),
            # filed under today's date. There's no way to compute a real, final
            # number for today until today is over — so don't show one.
            if row['date'] == date.today():
                continue
            standard_gwh = STANDARD_DAILY_FORECAST_GWH.get(row['date'].day, flat_daily_target_gwh)
            daily_target_gwh = alloc_map.get(str(row['date']), standard_gwh)
            actual_gwh = float(row['total_mwh'] or 0) / 1000
            ach = _pct(actual_gwh, daily_target_gwh)
            days.append({
                'date':          str(row['date']),
                'day':           row['date'].day,
                'target_gwh':    round(daily_target_gwh, 4),
                'actual_gwh':    round(actual_gwh, 4),
                'variance_gwh':  round(actual_gwh - daily_target_gwh, 4),
                'achievement_pct': round(ach, 1),
                'status':        _compliance(ach),
            })

        total_actual_gwh = sum(d['actual_gwh'] for d in days)
        mtd_ach = _pct(total_actual_gwh, monthly_target_gwh)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'monthly_target_gwh':  round(monthly_target_gwh, 4),
            'total_actual_gwh':    round(total_actual_gwh, 4),
            'mtd_achievement_pct': round(mtd_ach, 1),
            'mtd_status':          _compliance(mtd_ach),
            'days':                days,
        }

    # ── 11b. Daily Energy Forecast by Segment ───────────────────────────────────

    def get_daily_energy_by_segment(self):
        """
        Per-segment daily energy broken down by voltage level (33KV + 11KV).
        Segments: MDI | MDNI | Regions (from pl_segment field).
        Includes previous-month comparison for the same day numbers.
        Daily forecast = TMOMonthlySegmentTarget.target_energy_mwh / days_in_month.
        Actual uses balloon+system fallback via _daily_energy_breakdown.
        """
        # All onboarded feeders with a pl_segment, both voltages
        feeder_qs = self._base_feeder_qs().filter(pl_segment__isnull=False)

        # Build { segment → { '33kv': [ids], '11kv': [ids] } }
        seg_voltage: dict[str, dict[str, list]] = {
            'MDI':     {'33kv': [], '11kv': []},
            'MDNI':    {'33kv': [], '11kv': []},
            'Regions': {'33kv': [], '11kv': []},
        }
        for row in feeder_qs.values('id', 'pl_segment', 'voltage_level'):
            seg = row['pl_segment']
            vlt = '33kv' if row['voltage_level'] == '33kv' else '11kv'
            if seg in seg_voltage:
                seg_voltage[seg][vlt].append(row['id'])

        # 33KV feeders that supply downstream 11KV feeders — exclude to avoid double-counting
        upstream_33kv_ids = set(
            FeederSupplyRelationship.objects
            .filter(supplier_feeder__voltage_level='33kv', status='active')
            .values_list('supplier_feeder_id', flat=True)
        )

        def _safe_33kv(ids):
            return [fid for fid in ids if fid not in upstream_33kv_ids] if ids else []

        def _breakdown(ids, fd, td):
            return _daily_energy_breakdown(ids, fd, td) if ids else {}

        # Current period breakdowns
        curr = {}
        for seg, v in seg_voltage.items():
            ids_33 = _safe_33kv(v['33kv'])
            ids_11 = v['11kv']
            curr[seg] = {
                '33kv': _breakdown(ids_33, self.from_date, self.to_date),
                '11kv': _breakdown(ids_11, self.from_date, self.to_date),
            }

        # Previous month breakdowns (same day numbers for comparison bars)
        prev_month_last  = self.from_date.replace(day=1) - timedelta(days=1)
        prev_month_first = prev_month_last.replace(day=1)
        prev: dict[str, dict[str, dict]] = {}
        for seg, v in seg_voltage.items():
            ids_33 = _safe_33kv(v['33kv'])
            ids_11 = v['11kv']
            prev[seg] = {
                '33kv': _breakdown(ids_33, prev_month_first, prev_month_last),
                '11kv': _breakdown(ids_11, prev_month_first, prev_month_last),
            }

        # Scale retail-level per-day totals onto the bulk 33KV network total (same
        # population/method as get_daily_energy()), same rationale as
        # get_energy_by_segment()/get_energy_by_voltage(): the retail-level MDI/MDNI/
        # Regions split is only meaningful at that level, but the network's true
        # magnitude includes technical/distribution losses that only show up in the
        # bulk figure. Without this, this endpoint's MTD total silently diverges from
        # every other segment total on the dashboard.
        def _bulk_total_mwh(fd, td):
            bulk_ids = list(
                self._base_feeder_qs()
                .filter(voltage_level='33kv')
                .exclude(slug__in=DUPLICATE_FEEDER_SLUGS)
                .exclude(is_minigrid=True)
                .values_list('id', flat=True)
            )
            return calculate_energy_delivered(bulk_ids, fd, td)['total_mwh']

        def _scale_period(period: dict, fd, td):
            raw_total = sum(v for seg in period.values() for volt in seg.values() for v in volt.values())
            if not raw_total:
                return period
            factor = _bulk_total_mwh(fd, td) / raw_total
            return {
                seg: {volt: {d: mwh * factor for d, mwh in by_date.items()} for volt, by_date in v.items()}
                for seg, v in period.items()
            }

        curr = _scale_period(curr, self.from_date, self.to_date)
        prev = _scale_period(prev, prev_month_first, prev_month_last)

        # Index previous-month data by day number (1-31) for easy lookup
        def _by_day_number(voltage_daily: dict) -> dict[int, float]:
            out: dict[int, float] = {}
            for d_str, mwh in voltage_daily.items():
                day_num = int(d_str.split('-')[2])
                out[day_num] = out.get(day_num, 0.0) + mwh
            return out

        prev_by_day: dict[str, dict[str, dict[int, float]]] = {
            seg: {
                '33kv': _by_day_number(prev[seg]['33kv']),
                '11kv': _by_day_number(prev[seg]['11kv']),
            }
            for seg in seg_voltage
        }

        # Monthly targets
        days_in_month = calendar.monthrange(self.from_date.year, self.from_date.month)[1]
        target_qs = TMOMonthlySegmentTarget.objects.filter(
            year=self.from_date.year, month=self.from_date.month,
        )
        targets = {t.segment: float(t.target_energy_mwh) for t in target_qs}

        total_monthly_target = sum(targets.get(s, 0.0) for s in ('MDI', 'MDNI', 'Regions'))
        monthly_targets = {
            seg: {
                'target_mwh':         round(targets.get(seg, 0.0), 2),
                'daily_forecast_mwh': round(targets.get(seg, 0.0) / days_in_month, 4) if days_in_month else 0.0,
                'feeder_count_33kv':  len(seg_voltage[seg]['33kv']),
                'feeder_count_11kv':  len(seg_voltage[seg]['11kv']),
            }
            for seg in ('MDI', 'MDNI', 'Regions')
        }
        monthly_targets['Total'] = {
            'target_mwh':         round(total_monthly_target, 2),
            'daily_forecast_mwh': round(total_monthly_target / days_in_month, 4) if days_in_month else 0.0,
        }

        # Build day-by-day rows
        all_dates = []
        cur = self.from_date
        while cur <= self.to_date:
            all_dates.append(str(cur))
            cur += timedelta(days=1)

        days_out = []
        for d_str in sorted(all_dates):
            day_num = int(d_str.split('-')[2])
            segs_out: dict[str, dict] = {}
            tot_fore   = 0.0
            tot_actual = 0.0

            for seg in ('MDI', 'MDNI', 'Regions'):
                target     = targets.get(seg, 0.0)
                daily_fore = target / days_in_month if days_in_month else 0.0
                e_33 = curr[seg]['33kv'].get(d_str, 0.0)
                e_11 = curr[seg]['11kv'].get(d_str, 0.0)
                actual = e_33 + e_11
                ach    = _pct(actual, daily_fore)
                tot_fore   += daily_fore
                tot_actual += actual

                prev_33 = prev_by_day[seg]['33kv'].get(day_num, 0.0)
                prev_11 = prev_by_day[seg]['11kv'].get(day_num, 0.0)
                segs_out[seg] = {
                    'energy_33kv_mwh':      round(e_33, 4),
                    'energy_11kv_mwh':      round(e_11, 4),
                    'forecast_mwh':         round(daily_fore, 4),
                    'actual_mwh':           round(actual, 4),
                    'variance_mwh':         round(actual - daily_fore, 4),
                    'achievement_pct':      round(ach, 1),
                    'status':               _compliance(ach),
                    'prev_month': {
                        'energy_33kv_mwh': round(prev_33, 4),
                        'energy_11kv_mwh': round(prev_11, 4),
                        'total_mwh':       round(prev_33 + prev_11, 4),
                    },
                }

            tot_ach = _pct(tot_actual, tot_fore)
            days_out.append({
                'date':     d_str,
                'day':      day_num,
                'segments': segs_out,
                'total': {
                    'forecast_mwh':    round(tot_fore, 4),
                    'actual_mwh':      round(tot_actual, 4),
                    'variance_mwh':    round(tot_actual - tot_fore, 4),
                    'achievement_pct': round(tot_ach, 1),
                    'status':          _compliance(tot_ach),
                },
            })

        total_actual_mwh = sum(d['total']['actual_mwh'] for d in days_out)
        mtd_ach = _pct(total_actual_mwh, total_monthly_target)

        return {
            'period':              {'from': str(self.from_date), 'to': str(self.to_date)},
            'prev_month_period':   {'from': str(prev_month_first), 'to': str(prev_month_last)},
            'monthly_targets':     monthly_targets,
            'mtd_actual_mwh':      round(total_actual_mwh, 2),
            'mtd_achievement_pct': round(mtd_ach, 1),
            'mtd_status':          _compliance(mtd_ach),
            'days':                days_out,
        }

    # ── 12. PEAR (Premium Energy Allocation Ratio) ────────────────────────────

    def get_pear(self):
        """
        PEAR: MD (MDI+MDNI) vs NMD share of total energy.
        Compares yesterday vs MTD average, against the configured target mix.
        Covers Slide 10.
        """
        day       = self.to_date
        mtd_start = day.replace(day=1)

        feeder_qs  = self._base_feeder_qs()
        feeder_ids = set(feeder_qs.values_list('id', flat=True))
        md_ids     = (self.mdi_ids | self.mdni_ids) & feeder_ids
        nmd_ids    = feeder_ids - md_ids

        config = TMONetworkConfig.objects.filter(year=day.year, month=day.month).first()
        target_md_pct  = float(config.target_md_share_pct)  if config else 65.0
        target_nmd_pct = round(100.0 - target_md_pct, 2)

        def _e(ids, fd, td):
            if not ids:
                return 0.0
            return calculate_energy_delivered(self._energy_ids(list(ids)), fd, td)['total_mwh']

        def _tot(fd, td):
            return calculate_energy_delivered(self._energy_ids(list(feeder_ids)), fd, td)['total_mwh']

        day_total = _tot(day, day)
        day_md    = _e(md_ids, day, day)
        day_nmd   = max(day_total - day_md, 0.0)

        mtd_total = _tot(mtd_start, day)
        mtd_md    = _e(md_ids, mtd_start, day)
        mtd_nmd   = max(mtd_total - mtd_md, 0.0)

        return {
            'day':      str(day),
            'mtd_from': str(mtd_start),
            'target_mix': {
                'md_pct':  target_md_pct,
                'nmd_pct': target_nmd_pct,
            },
            'yesterday': {
                'total_mwh':    round(day_total, 2),
                'md_mwh':       round(day_md, 2),
                'nmd_mwh':      round(day_nmd, 2),
                'md_share_pct': round(_pct(day_md, day_total), 1),
                'nmd_share_pct': round(_pct(day_nmd, day_total), 1),
            },
            'mtd': {
                'total_mwh':    round(mtd_total, 2),
                'md_mwh':       round(mtd_md, 2),
                'nmd_mwh':      round(mtd_nmd, 2),
                'md_share_pct': round(_pct(mtd_md, mtd_total), 1),
                'nmd_share_pct': round(_pct(mtd_nmd, mtd_total), 1),
            },
        }

    # ── 13. Compliance Summary by Segment ────────────────────────────────────

    def get_compliance_summary(self):
        """
        Feeder count bucketed by supply-hours compliance status per DM segment.
        actual = avg daily hours of supply (DailyHoursOfSupply)
        target = admin-set TMOSupplyHoursTarget first, fallback to feeder.band.minimum_hours
        Segments: MDI | Non-MDI Band A | Non-MDI Non-Band A.
        Covers Slide 6 / Overall view1 chart.
        """
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))
        feeders    = {f.id: f for f in feeder_qs}

        # Avg daily hours of supply per feeder over the period
        supply_map = {
            row['feeder_id']: float(row['avg_hours'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg_hours=Avg('hours_supplied'))
        }

        # Admin-set segment targets for the period's month
        segment_target_map = {
            row.segment: float(row.target_hours)
            for row in TMOSupplyHoursTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
            )
        }

        # Per-feeder targets — current month first, then most-recent upload as base default
        month_targets = {
            str(row.feeder_id): float(row.target_hours)
            for row in TMOFeederSupplyTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
                feeder_id__in=feeder_ids,
            )
        }
        missing_ids = [fid for fid in feeder_ids if str(fid) not in month_targets]
        base_targets = {}
        if missing_ids:
            for row in (
                TMOFeederSupplyTarget.objects
                .filter(feeder_id__in=missing_ids)
                .order_by('feeder_id', '-year', '-month')
                .distinct('feeder_id')
                .values('feeder_id', 'target_hours')
            ):
                base_targets[str(row['feeder_id'])] = float(row['target_hours'])
        feeder_target_map = {**base_targets, **month_targets}

        def _dm_status(fid, feeder):
            if fid in self.mdi_ids:
                return 'MDI'
            is_band_a = feeder and feeder.band and feeder.band.slug == 'a'
            is_33kv   = feeder and feeder.voltage_level == '33kv'
            if is_band_a or is_33kv:
                return 'Non-MDI Band A'
            return 'Non-MDI, Non-Band A'

        BUCKETS = [
            ('exceeding',    lambda p: p > 105),
            ('on_target',    lambda p: 95 <= p <= 105),
            ('below_target', lambda p: 85 <= p < 95),
            ('poor',         lambda p: 75 <= p < 85),
            ('critical',     lambda p: p < 75),
        ]

        segments = {
            'MDI':                 {b: 0 for b, _ in BUCKETS},
            'Non-MDI Band A':      {b: 0 for b, _ in BUCKETS},
            'Non-MDI, Non-Band A': {b: 0 for b, _ in BUCKETS},
        }
        seg_totals = {k: 0 for k in segments}

        for fid in feeder_ids:
            feeder  = feeders.get(fid)
            avg_h   = supply_map.get(fid, 0.0)
            seg     = _dm_status(fid, feeder)
            # Priority: per-feeder upload > segment admin target > band minimum
            if str(fid) in feeder_target_map:
                min_h = feeder_target_map[str(fid)]
            elif seg in segment_target_map:
                min_h = segment_target_map[seg]
            else:
                min_h = float(feeder.band.minimum_hours) if feeder and feeder.band else 0.0
            pct = _pct(avg_h, min_h) if min_h else 0.0
            seg_totals[seg] += 1
            for bucket_name, test in BUCKETS:
                if test(pct):
                    segments[seg][bucket_name] += 1
                    break

        result = []
        for seg_name, counts in segments.items():
            total = seg_totals[seg_name]
            result.append({
                'segment':       seg_name,
                'total_feeders': total,
                'buckets': {
                    name: {
                        'count': counts[name],
                        'pct':   round(_pct(counts[name], total), 1) if total else 0.0,
                    }
                    for name, _ in BUCKETS
                },
            })

        return {
            'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
            'segments': result,
        }

    # ── 14. Energy by Voltage (33KV vs 11KV per segment) ─────────────────────

    def get_energy_by_voltage(self):
        """
        Per-segment daily energy split by voltage level (33KV vs 11KV),
        plus month-vs-previous-month totals.
        Covers Slides 13, 14, 15.
        """
        from datetime import date as date_type

        # Clamp to yesterday — today's cross-day diff is really yesterday's
        # energy filed under today's date (see get_daily_energy()), so the
        # month-comparison total must not include it either.
        to_date = min(self.to_date, date.today() - timedelta(days=1))

        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        # Build feeder → (segment, voltage) map
        feeders = {f.id: f for f in feeder_qs}
        fid_meta = {}
        for fid, feeder in feeders.items():
            seg = self._segment_label(fid)
            vol = feeder.voltage_level if feeder else ''
            fid_meta[fid] = (seg, vol)

        # Strip upstream 33KV feeders before energy sums to avoid double-counting
        energy_feeder_ids = self._energy_ids(feeder_ids)

        # Daily energy per feeder with balloon+system fallback logic
        meter_ids, balloon_ids = _classify_feeders(energy_feeder_ids, self.from_date, self.to_date)
        daily_rows = []
        if meter_ids:
            daily_rows.extend(
                EnergyDelivered.objects
                .filter(feeder_id__in=meter_ids, date__gte=self.from_date, date__lte=self.to_date)
                .values('date', 'feeder_id').annotate(mwh=Sum('energy_mwh'))
            )
        if balloon_ids:
            daily_rows.extend([
                {
                    'date': row['date'],
                    'feeder_id': row['feeder_id'],
                    'mwh': float(row['avg_load'] or 0) * int(row['supply_hours'] or 0),
                }
                for row in (
                    HourlyLoad.objects
                    .filter(feeder_id__in=balloon_ids, date__gte=self.from_date, date__lte=self.to_date, load_mw__gt=0)
                    .values('feeder_id', 'date').annotate(avg_load=Avg('load_mw'), supply_hours=Count('hour'))
                )
            ])

        # Aggregate by (date, segment, voltage)
        day_agg = defaultdict(lambda: defaultdict(lambda: {'33kv': 0.0, '11kv': 0.0}))
        for row in daily_rows:
            meta = fid_meta.get(row['feeder_id'])
            if not meta:
                continue
            seg, vol = meta
            mwh = float(row['mwh'] or 0)
            if vol == '33kv':
                day_agg[str(row['date'])][seg]['33kv'] += mwh
            else:
                day_agg[str(row['date'])][seg]['11kv'] += mwh

        days = []
        for d_str in sorted(day_agg):
            # Today's cross-day diff is really yesterday's energy filed under
            # today's date (see get_daily_energy()) — never show it as final.
            if d_str == str(date.today()):
                continue
            entry = {'date': d_str, 'day': int(d_str.split('-')[2]), 'segments': {}}
            for seg in ('MDI', 'MDNI', 'Regional'):
                v      = day_agg[d_str].get(seg, {'33kv': 0.0, '11kv': 0.0})
                mwh_33 = round(v['33kv'], 4)
                mwh_11 = round(v['11kv'], 4)
                entry['segments'][seg] = {
                    'energy_33kv_mwh': mwh_33,
                    'energy_11kv_mwh': mwh_11,
                    'total_mwh':       round(mwh_33 + mwh_11, 4),
                    'energy_33kv_gwh': round(mwh_33 / 1000, 4),
                    'energy_11kv_gwh': round(mwh_11 / 1000, 4),
                    'total_gwh':       round((mwh_33 + mwh_11) / 1000, 4),
                }
            days.append(entry)

        # Previous month totals
        y, m = self.from_date.year, self.from_date.month
        if m == 1:
            prev_y, prev_m = y - 1, 12
        else:
            prev_y, prev_m = y, m - 1
        prev_start = date_type(prev_y, prev_m, 1)
        prev_end   = date_type(prev_y, prev_m, calendar.monthrange(prev_y, prev_m)[1])

        def _vol_totals(fd, td):
            energy_per_feeder = calculate_energy_delivered_per_feeder(energy_feeder_ids, fd, td)
            seg_vol = defaultdict(lambda: {'33kv': 0.0, '11kv': 0.0})
            for fid, data in energy_per_feeder.items():
                meta = fid_meta.get(fid)
                if not meta:
                    continue
                seg, vol = meta
                seg_vol[seg]['33kv' if vol == '33kv' else '11kv'] += data['mwh']
            return seg_vol

        curr_totals = _vol_totals(self.from_date, to_date)
        prev_totals = _vol_totals(prev_start, prev_end)

        # Scale retail-level totals onto the bulk 33KV network total (same population/
        # method as get_daily_energy()), same rationale as get_energy_by_segment(): MDI/
        # MDNI/Regions and 33KV/11KV splits are only meaningful at the retail level, but
        # the network's true magnitude includes technical/distribution losses that only
        # show up in the bulk figure. Scaling preserves the retail-derived proportions
        # while making the grand total reconcile with Daily Energy Allocation.
        def _bulk_total_mwh(fd, td):
            bulk_ids = list(
                self._base_feeder_qs()
                .filter(voltage_level='33kv')
                .exclude(slug__in=DUPLICATE_FEEDER_SLUGS)
                .exclude(is_minigrid=True)
                .values_list('id', flat=True)
            )
            return calculate_energy_delivered(bulk_ids, fd, td)['total_mwh']

        def _scale(totals, fd, td):
            raw_total = sum(v for seg in totals.values() for v in seg.values())
            if not raw_total:
                return totals
            factor = _bulk_total_mwh(fd, td) / raw_total
            return {
                seg: {vol: mwh * factor for vol, mwh in v.items()}
                for seg, v in totals.items()
            }

        curr_totals = _scale(curr_totals, self.from_date, to_date)
        prev_totals = _scale(prev_totals, prev_start, prev_end)

        # Apply the same current-month scale factor to the daily series so the "days"
        # breakdown reconciles with the (now-scaled) month_comparison totals.
        curr_raw_total = sum(
            vol_val
            for d_str in day_agg
            if d_str != str(date.today())
            for seg in ('MDI', 'MDNI', 'Regional')
            for vol_val in day_agg[d_str].get(seg, {'33kv': 0.0, '11kv': 0.0}).values()
        )
        if curr_raw_total:
            day_scale_factor = _bulk_total_mwh(self.from_date, to_date) / curr_raw_total
            for entry in days:
                for seg in ('MDI', 'MDNI', 'Regional'):
                    s = entry['segments'][seg]
                    mwh_33 = s['energy_33kv_mwh'] * day_scale_factor
                    mwh_11 = s['energy_11kv_mwh'] * day_scale_factor
                    entry['segments'][seg] = {
                        'energy_33kv_mwh': round(mwh_33, 4),
                        'energy_11kv_mwh': round(mwh_11, 4),
                        'total_mwh':       round(mwh_33 + mwh_11, 4),
                        'energy_33kv_gwh': round(mwh_33 / 1000, 4),
                        'energy_11kv_gwh': round(mwh_11 / 1000, 4),
                        'total_gwh':       round((mwh_33 + mwh_11) / 1000, 4),
                    }

        month_comparison = {}
        for seg in ('MDI', 'MDNI', 'Regional'):
            c = curr_totals.get(seg, {'33kv': 0.0, '11kv': 0.0})
            p = prev_totals.get(seg, {'33kv': 0.0, '11kv': 0.0})

            def _vol(v33, v11):
                return {
                    'energy_33kv_mwh': round(v33, 2),
                    'energy_11kv_mwh': round(v11, 2),
                    'total_mwh':       round(v33 + v11, 2),
                    'energy_33kv_gwh': round(v33 / 1000, 4),
                    'energy_11kv_gwh': round(v11 / 1000, 4),
                    'total_gwh':       round((v33 + v11) / 1000, 4),
                }

            month_comparison[seg] = {
                'current_month':  _vol(c['33kv'], c['11kv']),
                'previous_month': _vol(p['33kv'], p['11kv']),
            }

        return {
            'period':           {'from': str(self.from_date), 'to': str(self.to_date)},
            'days':             days,
            'month_comparison': month_comparison,
        }

    # ── 15. Techno-Commercial Incidents ──────────────────────────────────────

    def get_incidents(self):
        """
        Weekly Techno-Commercial Incidence report driven by FeederInterruption.
        Financial loss is calculated automatically:
            Loss (₦) = avg_load_mw × duration_hours × 1,000 × tariff_per_kwh
        Where:
            avg_load_mw   = feeder's 30-day average MW (non-zero hours from HourlyLoad)
            duration_hours = occurred_at → restored_at (or now if still lingering)
            tariff_per_kwh = from TMOMonthlySegmentTarget for feeder's pl_segment
        Covers Slide 16.
        """
        from django.utils import timezone as tz

        qs = (
            FeederInterruption.objects
            .filter(
                occurred_at__date__gte=self.from_date,
                occurred_at__date__lte=self.to_date,
            )
            .select_related(
                'feeder', 'feeder__band',
                'feeder__substation', 'feeder__substation__state',
                'feeder__business_district',
            )
        )

        f = self.filters
        if f.get('state'):
            qs = qs.filter(feeder__substation__state__slug=f['state'])
        if f.get('district'):
            qs = qs.filter(feeder__business_district__slug=f['district'])
        if f.get('feeder'):
            qs = qs.filter(feeder__slug=f['feeder'])
        if f.get('segment'):
            seg = f['segment'].upper()
            if seg == 'MDI':
                qs = qs.filter(feeder_id__in=self.mdi_ids)
            elif seg == 'MDNI':
                qs = qs.filter(feeder_id__in=self.mdni_ids)
        if f.get('status'):
            s = f['status'].lower()
            if s == 'rectified':
                qs = qs.exclude(restored_at=None)
            elif s == 'lingering':
                qs = qs.filter(restored_at=None)

        # Build tariff lookup: pl_segment → ₦/kWh
        tariff_map = {
            t.segment: float(t.average_tariff_per_kwh)
            for t in TMOMonthlySegmentTarget.objects.filter(
                year=self.from_date.year, month=self.from_date.month,
            )
        }

        # Build feeder avg-load lookup over past 30 days (non-zero hours only)
        feeder_ids = list(qs.values_list('feeder_id', flat=True).distinct())
        avg_load_30d: dict = {}
        if feeder_ids:
            lookback = self.to_date - timedelta(days=30)
            for row in (
                HourlyLoad.objects
                .filter(feeder_id__in=feeder_ids, date__gte=lookback,
                        date__lte=self.to_date, load_mw__gt=0)
                .values('feeder_id')
                .annotate(avg_mw=Avg('load_mw'))
            ):
                avg_load_30d[row['feeder_id']] = float(row['avg_mw'] or 0)

        now = tz.now()
        rows = []
        total_loss = 0.0
        rectified  = 0
        lingering  = 0

        for obj in qs:
            feeder  = obj.feeder
            is_done = obj.restored_at is not None
            status  = 'rectified' if is_done else 'lingering'

            # Duration
            end_time  = obj.restored_at if is_done else now
            occurred  = obj.occurred_at if tz.is_aware(obj.occurred_at) else tz.make_aware(obj.occurred_at)
            end_aware = end_time if tz.is_aware(end_time) else tz.make_aware(end_time)
            duration_hrs = max((end_aware - occurred).total_seconds() / 3600, 0)

            # Average load (MW) — fallback to 0 if no HourlyLoad data
            avg_mw = avg_load_30d.get(feeder.id, 0.0)

            # Tariff — use feeder's pl_segment, fall back to band order
            seg = feeder.pl_segment or 'Regions'
            tariff = tariff_map.get(seg, tariff_map.get('Regions', 0.0))

            # Financial loss = energy not served × tariff
            energy_not_served_mwh = avg_mw * duration_hrs
            loss = energy_not_served_mwh * 1_000 * tariff

            total_loss += loss
            if is_done:
                rectified += 1
            else:
                lingering += 1

            rows.append({
                'id':                   str(obj.id),
                'feeder_name':          feeder.name,
                'feeder_slug':          feeder.slug,
                'voltage':              feeder.voltage_level,
                'pl_segment':           seg,
                'coordinate':           feeder.substation.state.name if feeder.substation and feeder.substation.state else '',
                'region':               feeder.business_district.name if feeder.business_district else '',
                'nature_of_fault':      obj.get_interruption_type_display() + (f' — {obj.description}' if obj.description else ''),
                'interruption_type':    obj.interruption_type,
                'status':               status,
                'occurred_at':          obj.occurred_at.isoformat(),
                'restored_at':          obj.restored_at.isoformat() if obj.restored_at else None,
                'duration_hours':       round(duration_hrs, 2),
                'avg_load_mw':          round(avg_mw, 4),
                'energy_not_served_mwh': round(energy_not_served_mwh, 4),
                'tariff_per_kwh':       tariff,
                'financial_loss_ngn':   round(loss, 2),
            })

        total = rectified + lingering
        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'summary': {
                'total_incidents':          total,
                'rectified':                rectified,
                'lingering':                lingering,
                'rectification_rate_pct':   round(_pct(rectified, total), 1),
                'total_financial_loss_ngn': round(total_loss, 2),
            },
            'incidents': rows,
        }

    # ── 16. GCR (Energy Gap-to-Cost Ratio) ───────────────────────────────────

    def get_gcr(self):
        """
        P&L Target vs Billing Value Realization — Energy Gap-to-Cost Ratio.
        Uses TMOMonthlySegmentTarget.average_tariff_per_kwh to compute bill values.
        Covers Slide 18.
        """
        feeder_ids = set(self._base_feeder_qs().values_list('id', flat=True))

        target_qs = TMOMonthlySegmentTarget.objects.filter(
            year=self.from_date.year,
            month=self.from_date.month,
        )
        targets = {t.segment: t for t in target_qs}

        # Fallback: if current month has no targets, use the most recent available month
        if not targets:
            latest = (
                TMOMonthlySegmentTarget.objects
                .order_by('-year', '-month')
                .values_list('year', 'month')
                .first()
            )
            if latest:
                targets = {
                    t.segment: t
                    for t in TMOMonthlySegmentTarget.objects.filter(
                        year=latest[0], month=latest[1],
                    )
                }

        # Reuse get_energy_by_segment()'s actual_mwh — it's the single source of truth
        # for "actual energy consumed per segment" (retail-level classification, scaled
        # onto the bulk network total). Previously this method recomputed its own 33KV-
        # only, unstripped total here, which double-counted district bulk feeders (e.g.
        # BRISCOE, ZARIA ROAD) that also supply metered downstream 11KV feeders — that
        # produced a "consumed_gwh" wildly inconsistent with the actual-consumption
        # slide for the same segment/period.
        seg_actual_mwh = {s['segment']: s['actual_mwh'] for s in self.get_energy_by_segment()['segments']}

        def _actual(seg_name):
            return seg_actual_mwh.get(seg_name, 0.0)

        seg_data = [
            ('MDI',     self.mdi_ids & feeder_ids),
            ('MDNI',    self.mdni_ids & feeder_ids),
            ('Regions', feeder_ids - self.mdi_ids - self.mdni_ids),
        ]

        rows = []
        grand_target_mwh   = 0.0
        grand_actual_mwh   = 0.0
        grand_exp_bill     = 0.0
        grand_mtd_bill     = 0.0

        for seg_name, ids in seg_data:
            t = targets.get(seg_name)
            target_mwh  = float(t.target_energy_mwh) if t else 0.0
            tariff      = float(t.average_tariff_per_kwh) if t else 0.0
            actual_mwh  = _actual(seg_name)
            gap_mwh     = target_mwh - actual_mwh
            # tariff is ₦/kWh; convert MWh → kWh before multiplying
            exp_bill    = target_mwh * 1_000 * tariff
            mtd_bill    = actual_mwh * 1_000 * tariff
            gap_bill    = exp_bill - mtd_bill
            mtd_ach     = _pct(actual_mwh, target_mwh)

            grand_target_mwh += target_mwh
            grand_actual_mwh += actual_mwh
            grand_exp_bill   += exp_bill
            grand_mtd_bill   += mtd_bill

            rows.append({
                'segment':              seg_name,
                'target_gwh':           round(target_mwh / 1000, 4),
                'consumed_gwh':         round(actual_mwh / 1000, 4),
                'gap_gwh':              round(gap_mwh / 1000, 4),
                'expected_bill_value':  round(exp_bill, 2),
                'mtd_bill_value':       round(mtd_bill, 2),
                'gap_bill_value':       round(gap_bill, 2),
                'mtd_achievement_pct':  round(mtd_ach, 1),
                'gap_pct':              round(100 - mtd_ach, 1),
                'average_tariff_per_kwh': tariff,
            })

        grand_ach = _pct(grand_actual_mwh, grand_target_mwh)
        rows.append({
            'segment':              'Total',
            'target_gwh':           round(grand_target_mwh / 1000, 4),
            'consumed_gwh':         round(grand_actual_mwh / 1000, 4),
            'gap_gwh':              round((grand_target_mwh - grand_actual_mwh) / 1000, 4),
            'expected_bill_value':  round(grand_exp_bill, 2),
            'mtd_bill_value':       round(grand_mtd_bill, 2),
            'gap_bill_value':       round(grand_exp_bill - grand_mtd_bill, 2),
            'mtd_achievement_pct':  round(grand_ach, 1),
            'gap_pct':              round(100 - grand_ach, 1),
            'average_tariff_per_kwh': None,
        })

        return {
            'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
            'rows':     rows,
            'segments': rows,   # alias used by report renderers and GCR slide
        }

    # ── 17. Monitored New Feeders — daily energy from commissioning date ─────

    def get_monitored_feeders(self):
        """
        New feeders under active monitoring (Feeder.monitoring_end_date >= today).
        Each feeder returns daily MWh from its onboarded_at date to today,
        so the TMO dashboard can show the "Dawanau feeder: Daily Energy Allocation" style chart.
        """
        today = date.today()
        qs = (
            Feeder.objects
            .filter(is_onboarded=True, monitoring_end_date__gte=today)
            .select_related('band', 'substation', 'substation__state', 'business_district')
        )

        feeders_out = []
        for feeder in qs:
            # Monitor from onboarding date (or self.from_date if later)
            monitor_start = (
                feeder.onboarded_at.date() if feeder.onboarded_at else self.from_date
            )
            from_d = max(monitor_start, self.from_date)
            to_d   = self.to_date

            day_map   = _feeder_energy_by_day(feeder.id, from_d, to_d)
            total_mwh = sum(day_map.values())

            # Build day-by-day array covering full monitoring window
            days = []
            cur  = from_d
            while cur <= to_d:
                d_str = str(cur)
                days.append({
                    'date': d_str,
                    'day':  cur.day,
                    'mwh':  round(day_map.get(d_str, 0.0), 2),
                })
                cur += timedelta(days=1)

            feeders_out.append({
                'feeder_id':          str(feeder.id),
                'feeder_name':        feeder.name,
                'feeder_slug':        feeder.slug,
                'voltage_level':      feeder.voltage_level,
                'band':               feeder.band.name if feeder.band else '',
                'state':              (feeder.substation.state.name
                                      if feeder.substation and feeder.substation.state else ''),
                'district':           feeder.business_district.name if feeder.business_district else '',
                'onboarded_at':       str(feeder.onboarded_at.date()) if feeder.onboarded_at else None,
                'monitoring_end_date': str(feeder.monitoring_end_date),
                'monitoring_from':    str(from_d),
                'total_mwh':          round(total_mwh, 2),
                'days':               days,
            })

        return {
            'as_of':          str(today),
            'feeder_count':   len(feeders_out),
            'feeders':        feeders_out,
        }

    # ── 18. Minigrids Daily (SSF — per-feeder daily MWh + summary table) ────

    def get_minigrids_daily(self, feeder_slug=None, q=None, band=None, segment=None):
        """
        Per-feeder daily MWh chart.
        If feeder_slug / q / band / segment provided → any matching feeder.
        Default (no params): minigrid feeders only (original SSF behaviour).
        """
        feeder_qs = self._base_feeder_qs()
        if feeder_slug:
            feeder_qs = feeder_qs.filter(slug=feeder_slug)
        elif q or band or segment:
            if q:
                feeder_qs = feeder_qs.filter(name__icontains=q)
            if band:
                feeder_qs = feeder_qs.filter(band__slug__iexact=band)
            if segment:
                feeder_qs = feeder_qs.filter(pl_segment__iexact=segment)
        else:
            feeder_qs = feeder_qs.filter(is_minigrid=True)
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        if not feeder_ids:
            return {
                'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
                'feeders':  [],
                'summary':  {'total_mwh': 0.0, 'days': []},
            }

        # All dates in the period
        all_dates = []
        cur = self.from_date
        while cur <= self.to_date:
            all_dates.append(str(cur))
            cur += timedelta(days=1)

        # Per-feeder daily energy (balloon+system fallback)
        feeders_out = []
        summary_by_date = defaultdict(float)

        for feeder in feeder_qs:
            day_map   = _feeder_energy_by_day(feeder.id, self.from_date, self.to_date)
            total_mwh = sum(day_map.values())

            days = []
            for d_str in all_dates:
                mwh = day_map.get(d_str, 0.0)
                summary_by_date[d_str] += mwh
                days.append({
                    'date': d_str,
                    'day':  int(d_str.split('-')[2]),
                    'mwh':  round(mwh, 2),
                })

            feeders_out.append({
                'feeder_id':   str(feeder.id),
                'feeder_name': feeder.name,
                'feeder_slug': feeder.slug,
                'state':       (feeder.substation.state.name
                                if feeder.substation and feeder.substation.state else ''),
                'total_mwh':   round(total_mwh, 2),
                'days':        days,
            })

        # Sort by total MWh descending
        feeders_out.sort(key=lambda f: f['total_mwh'], reverse=True)

        # Summary table — all minigrids combined per day
        summary_days = [
            {
                'date': d_str,
                'day':  int(d_str.split('-')[2]),
                'mwh':  round(summary_by_date[d_str], 2),
            }
            for d_str in all_dates
        ]
        grand_total = round(sum(summary_by_date.values()), 2)

        return {
            'period':  {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': feeders_out,          # one entry per minigrid → individual bar charts
            'summary': {                     # aggregated → combined table
                'total_mwh': grand_total,
                'days':      summary_days,
            },
        }

    # ── 18. Daily Real-Time Allocation (TCN vs Actual Consumption) ───────────

    def get_daily_allocation(self):
        """
        Per-day comparison of KEDCO allocation vs DISCO offtake from TMONetworkDispatch.
        Uses the same data source as the live dashboard (tmo_tmonetworkdispatch table).
        kedco_allocation_mw → expected_mw
        disco_offtake_mw    → actual_mw
        variance_mw         → unpicked_mw (positive = underpicked, negative = overrun)
        """
        dispatch_map = {
            str(obj.date): obj
            for obj in TMONetworkDispatch.objects.filter(
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).order_by('date')
        }

        all_dates = set(dispatch_map.keys())
        cur = self.from_date
        while cur <= self.to_date:
            all_dates.add(str(cur))
            cur += timedelta(days=1)

        days = []
        for d_str in sorted(all_dates):
            # Today's row is only ever a partial-day average of whatever hours have
            # been entered in the sheet so far — not a finished day. Don't show it
            # as if it were final, same rule as get_daily_energy().
            if d_str == str(date.today()):
                continue
            obj      = dispatch_map.get(d_str)
            expected = float(obj.kedco_allocation_mw or 0) if obj else 0.0
            actual   = float(obj.disco_offtake_mw    or 0) if obj else 0.0
            unpicked = float(obj.variance_mw         or 0) if obj else 0.0
            days.append({
                'date':        d_str,
                'day':         int(d_str.split('-')[2]),
                'expected_mw': round(expected, 2),
                'actual_mw':   round(actual,   2),
                'unpicked_mw': round(unpicked,  2),
            })

        total_expected = sum(d['expected_mw'] for d in days)
        total_actual   = sum(d['actual_mw']   for d in days)
        total_unpicked = total_expected - total_actual

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'summary': {
                'total_expected_mw':  round(total_expected, 2),
                'total_actual_mw':    round(total_actual,   2),
                'total_unpicked_mw':  round(total_unpicked, 2),
            },
            'days': days,
        }

    # ── 18. Single Feeder Detail ─────────────────────────────────────────────

    def get_feeder_detail(self, feeder_slug):
        feeder = Feeder.objects.select_related(
            'band', 'substation', 'substation__state', 'business_district'
        ).get(slug=feeder_slug, is_onboarded=True)

        energy_by_day = _feeder_energy_by_day(feeder.id, self.from_date, self.to_date)

        hours_map = {
            str(row['date']): float(row['hours_supplied'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder=feeder,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('date', 'hours_supplied')
        }

        target_map = {
            str(row['target_date']): float(row['target_mwh'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder=feeder,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('target_date', 'target_mwh')
        }

        days = []
        for d_str in sorted(energy_by_day):
            actual = energy_by_day[d_str]
            target = target_map.get(d_str, 0.0)
            hours  = hours_map.get(d_str, 0.0)
            ach    = _pct(actual, target)
            days.append({
                'date':           d_str,
                'target_mwh':     round(target, 4),
                'actual_mwh':     round(actual, 4),
                'variance_mwh':   round(actual - target, 4),
                'achievement_pct': round(ach, 1),
                'hours_supplied': round(hours, 2),
                'status':         _compliance(ach),
            })

        total_actual = sum(d['actual_mwh'] for d in days)
        total_target = sum(d['target_mwh'] for d in days)
        ov_ach       = _pct(total_actual, total_target)

        return {
            'feeder': {
                'id':            str(feeder.id),
                'name':          feeder.name,
                'slug':          feeder.slug,
                'segment':       self._segment_label(feeder.id),
                'band':          feeder.band.name if feeder.band else '',
                'voltage_level': feeder.voltage_level,
                'state':         (feeder.substation.state.name
                                 if feeder.substation and feeder.substation.state else ''),
                'district':      feeder.business_district.name if feeder.business_district else '',
                'is_minigrid':   feeder.is_minigrid,
            },
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'days':   days,
            'summary': {
                'total_target_mwh':        round(total_target, 2),
                'total_actual_mwh':        round(total_actual, 2),
                'variance_mwh':            round(total_actual - total_target, 2),
                'overall_achievement_pct': round(ov_ach, 1),
                'overall_status':          _compliance(ov_ach),
            },
        }
