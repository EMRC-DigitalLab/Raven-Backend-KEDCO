# tmo/services.py
from datetime import date, timedelta

from django.db.models import Avg, Count, Sum

from commercial.models import (
    TMOBillingEfficiency,
    TMOCollectionTarget,
    TMOFeederTarget,
)
from commercial.models import CommercialCustomer
from common.models import Band, Feeder
from technical.models import DailyHoursOfSupply, EnergyDelivered
from tmo.models import TMOMonthlySegmentTarget


# ── Helpers ──────────────────────────────────────────────────────────────────

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
    if pct >= 100:
        return 'on_target'
    if pct >= 90:
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
    MDI takes priority: if a feeder has any MDI customer, it is MDI.
    MDNI: feeders with MDNI customers but no MDI customers.
    Returns (mdi_ids set, mdni_ids set).
    """
    mdi_ids = set(
        CommercialCustomer.objects
        .filter(customer_type='MDI')
        .values_list('feeder_id', flat=True)
        .distinct()
    )
    mdni_ids = set(
        CommercialCustomer.objects
        .filter(customer_type='MDNI')
        .exclude(feeder_id__in=mdi_ids)
        .values_list('feeder_id', flat=True)
        .distinct()
    )
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
        self.from_date = from_date
        self.to_date   = to_date
        self.filters   = filters or {}
        self._mdi_ids  = None
        self._mdni_ids = None

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

        energy_agg = EnergyDelivered.objects.filter(
            feeder_id__in=feeder_ids,
            date__gte=self.from_date,
            date__lte=self.to_date,
        ).aggregate(total=Sum('energy_mwh'))
        total_actual = float(energy_agg['total'] or 0)

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

        actuals = {
            row['feeder_id']: float(row['total'] or 0)
            for row in EnergyDelivered.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('energy_mwh'))
        }

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
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = set(feeder_qs.values_list('id', flat=True))

        minigrid_ids = set(feeder_qs.filter(is_minigrid=True).values_list('id', flat=True))

        buckets = {
            'MDI':      self.mdi_ids & feeder_ids,
            'MDNI':     self.mdni_ids & feeder_ids,
            'Minigrid': minigrid_ids,
        }

        def _energy(ids):
            if not ids:
                return 0.0
            return float(
                EnergyDelivered.objects.filter(
                    feeder_id__in=ids,
                    date__gte=self.from_date,
                    date__lte=self.to_date,
                ).aggregate(t=Sum('energy_mwh'))['t'] or 0
            )

        def _target(ids):
            if not ids:
                return 0.0
            return float(
                TMOFeederTarget.objects.filter(
                    feeder_id__in=ids,
                    target_date__gte=self.from_date,
                    target_date__lte=self.to_date,
                ).aggregate(t=Sum('target_mwh'))['t'] or 0
            )

        segments = []
        for name, ids in buckets.items():
            actual = _energy(ids)
            target = _target(ids)
            ach    = _pct(actual, target)
            segments.append({
                'segment':          name,
                'feeder_count':     len(ids),
                'target_mwh':       round(target, 2),
                'actual_mwh':       round(actual, 2),
                'variance_mwh':     round(actual - target, 2),
                'achievement_pct':  round(ach, 1),
                'status':           _compliance(ach),
            })

        return {
            'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
            'segments': segments,
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
        rows = []
        for fid in feeder_ids:
            feeder = feeders.get(fid)
            s      = supply_map.get(fid, {})
            avg_h  = float(s.get('avg_hours') or 0)
            min_h  = float(feeder.band.minimum_hours) if feeder and feeder.band else 0.0
            c_pct  = _pct(avg_h, min_h) if min_h else 0.0
            rows.append({
                'feeder_id':          str(fid),
                'feeder_name':        feeder.name if feeder else str(fid),
                'segment':            self._segment_label(fid),
                'band':               feeder.band.name if feeder and feeder.band else '',
                'band_minimum_hours': min_h,
                'avg_daily_hours':    round(avg_h, 2),
                'total_hours':        round(float(s.get('total_hours') or 0), 2),
                'days_recorded':      s.get('days', 0),
                'compliance_pct':     round(c_pct, 1),
                'status':             _compliance(c_pct),
                'is_minigrid':        feeder.is_minigrid if feeder else False,
            })

        rows.sort(key=lambda r: r['compliance_pct'])
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
            return float(
                EnergyDelivered.objects.filter(
                    feeder_id__in=ids,
                    date__gte=self.from_date,
                    date__lte=self.to_date,
                ).aggregate(t=Sum('energy_mwh'))['t'] or 0
            )

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

        actuals = {
            row['feeder_id']: float(row['total'] or 0)
            for row in EnergyDelivered.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('energy_mwh'))
        }
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

        actuals = {
            row['feeder_id']: float(row['total'] or 0)
            for row in EnergyDelivered.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('energy_mwh'))
        }
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

    # ── 10. Single Feeder Detail ─────────────────────────────────────────────

    def get_feeder_detail(self, feeder_slug):
        feeder = Feeder.objects.select_related(
            'band', 'substation', 'substation__state', 'business_district'
        ).get(slug=feeder_slug, is_onboarded=True)

        daily_energy = list(
            EnergyDelivered.objects.filter(
                feeder=feeder,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).order_by('date').values('date', 'energy_mwh')
        )

        hours_map = {
            row['date']: float(row['hours_supplied'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder=feeder,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('date', 'hours_supplied')
        }

        target_map = {
            row['target_date']: float(row['target_mwh'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder=feeder,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('target_date', 'target_mwh')
        }

        days = []
        for row in daily_energy:
            d      = row['date']
            actual = float(row['energy_mwh'] or 0)
            target = target_map.get(d, 0.0)
            hours  = hours_map.get(d, 0.0)
            ach    = _pct(actual, target)
            days.append({
                'date':           str(d),
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
