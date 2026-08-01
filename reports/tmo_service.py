"""
reports/tmo_service.py

TMO Report Data Service — thin adapter over tmo.services.TMOService.

The live dashboard service (tmo/services.py) owns all computation.
This module just maps each report section_type to the right TMOService method
and holds the dispatcher used by ReportDataService.get_all_section_data().

Section types exposed:
  tmo_overview                — Header KPIs (energy achievement + supply compliance)
  tmo_daily_network_energy    — Daily Forecast vs Actual GWh (Slides 2 & 3 combined)
  tmo_daily_energy_consumed   — Daily Energy Consumed single-series view (same source)
  tmo_daily_allocation        — Allocation vs Consumption diverging bar
  tmo_feeder_compliance_table — Per-feeder supply compliance criticality table
  tmo_compliance_by_segment   — Compliance buckets stacked by segment
  tmo_minigrids_daily         — Daily MWh per feeder (default: minigrids)
  tmo_pear                    — PEAR target mix + actual mix
  tmo_energy_pnl_donut        — Energy by P&L segment (MDI / MDNI / Regions)
  tmo_energy_by_voltage       — Per-segment daily 33KV + 11KV bars
  tmo_incidents               — Incidents table
  tmo_pnl_deficit             — P&L Target Realization Deficit
  tmo_gcr                     — GCR table (P&L target vs billing value)
  tmo_volatility              — P&L Mix Volatility Index table

Legacy sections (still routed here for backward compat):
  tmo_feeder_dispatch         — original TMOFeederTarget vs EnergyDelivered
  tmo_collection_performance  — TMOCollectionTarget by segment
  tmo_billing_efficiency      — TMOBillingEfficiency by scope
"""

from datetime import datetime


def _parse_date(val):
    if isinstance(val, str):
        return datetime.strptime(val.split('T')[0].strip(), '%Y-%m-%d').date()
    if isinstance(val, datetime):
        return val.date()
    return val


def _pct(numerator, denominator):
    try:
        return round(float(numerator) / float(denominator) * 100, 2)
    except (ZeroDivisionError, TypeError):
        return 0.0


def _compliance(pct):
    if pct >= 100: return 'on_target'
    if pct >= 90:  return 'below_target'
    if pct >= 75:  return 'poor'
    return 'critical'


# ── Legacy TMO models (kept for old report templates) ─────────────────────────

def _legacy_feeder_dispatch(from_date, to_date, feeder_ids):
    from django.db.models import Sum
    from commercial.models import TMOFeederTarget
    from technical.models import EnergyDelivered

    targets_qs = TMOFeederTarget.objects.filter(
        target_date__gte=from_date,
        target_date__lte=to_date,
    )
    if feeder_ids:
        targets_qs = targets_qs.filter(feeder_id__in=feeder_ids)

    targets_by_code = {}
    for row in targets_qs.values('feeder_code', 'feeder__name', 'feeder__slug', 'feeder_id').annotate(
        total_target=Sum('target_mwh')
    ):
        targets_by_code[row['feeder_code']] = {
            'feeder_id':   str(row['feeder_id']) if row['feeder_id'] else row['feeder_code'],
            'feeder_name': row['feeder__name'] or row['feeder_code'],
            'feeder_slug': row['feeder__slug'] or '',
            'target_mwh':  float(row['total_target'] or 0),
        }

    actuals_qs = EnergyDelivered.objects.filter(date__gte=from_date, date__lte=to_date)
    if feeder_ids:
        actuals_qs = actuals_qs.filter(feeder_id__in=feeder_ids)

    actuals_by_slug = {}
    for row in actuals_qs.values('feeder__slug', 'feeder__name').annotate(total=Sum('energy_mwh')):
        slug = row['feeder__slug'] or ''
        actuals_by_slug[slug] = {
            'total': float(row['total'] or 0),
            'name':  row['feeder__name'] or slug,
        }

    slug_to_code = {v['feeder_slug']: k for k, v in targets_by_code.items() if v['feeder_slug']}
    all_keys = set(targets_by_code) | {slug_to_code.get(s, s) for s in actuals_by_slug}

    rows = []
    for code in sorted(all_keys):
        info = targets_by_code.get(code, {})
        slug = info.get('feeder_slug', code)
        actual_entry = actuals_by_slug.get(slug, {})
        target_mwh = info.get('target_mwh', 0.0)
        actual_mwh = actual_entry.get('total', 0.0)
        ach = _pct(actual_mwh, target_mwh) if target_mwh else 0.0
        rows.append({
            'feeder_id':       info.get('feeder_id', code),
            'feeder_name':     info.get('feeder_name') or actual_entry.get('name') or code,
            'target_mwh':      target_mwh,
            'actual_mwh':      actual_mwh,
            'variance_mwh':    round(actual_mwh - target_mwh, 4),
            'achievement_pct': ach,
            'status':          _compliance(ach),
        })

    total_target = sum(r['target_mwh'] for r in rows)
    total_actual = sum(r['actual_mwh'] for r in rows)
    return {
        'feeders': rows,
        'total_target_mwh': round(total_target, 4),
        'total_actual_mwh': round(total_actual, 4),
        'overall_achievement_pct': _pct(total_actual, total_target),
        'overall_status': _compliance(_pct(total_actual, total_target)),
    }


def _legacy_collection_performance(from_date, to_date):
    from commercial.models import TMOCollectionTarget
    qs = TMOCollectionTarget.objects.filter(
        period_month__gte=from_date,
        period_month__lte=to_date,
    ).order_by('period_month', 'segment_code', 'sub_segment')
    rows = []
    for obj in qs:
        target = float(obj.target_amount)
        actual = float(obj.actual_amount)
        ach = _pct(actual, target)
        rows.append({
            'segment_code': obj.segment_code,
            'sub_segment': obj.sub_segment,
            'period_month': str(obj.period_month),
            'target_amount': target,
            'actual_amount': actual,
            'variance': round(actual - target, 2),
            'achievement_pct': ach,
            'status': _compliance(ach),
        })
    total_target = sum(r['target_amount'] for r in rows)
    total_actual = sum(r['actual_amount'] for r in rows)
    return {
        'segments': rows,
        'total_target': round(total_target, 2),
        'total_actual': round(total_actual, 2),
        'overall_achievement_pct': _pct(total_actual, total_target),
    }


def _legacy_billing_efficiency(from_date, to_date):
    from commercial.models import TMOBillingEfficiency
    qs = TMOBillingEfficiency.objects.filter(
        period_month__gte=from_date,
        period_month__lte=to_date,
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
            'billing_efficiency_pct': be_pct,
            'target_revenue_amount':  t_rev,
            'billed_amount':          b_rev,
            'revenue_efficiency_pct': rr_pct,
        })
    total_del  = sum(r['energy_delivered_gwh']  for r in rows)
    total_bil  = sum(r['energy_billed_gwh']     for r in rows)
    total_trev = sum(r['target_revenue_amount'] for r in rows)
    total_brev = sum(r['billed_amount']         for r in rows)
    return {
        'rows': rows,
        'total_energy_delivered_gwh': round(total_del, 4),
        'total_energy_billed_gwh':    round(total_bil, 4),
        'overall_billing_eff_pct':    _pct(total_bil, total_del),
        'total_target_revenue':       round(total_trev, 2),
        'total_billed_amount':        round(total_brev, 2),
        'overall_revenue_eff_pct':    _pct(total_brev, total_trev),
    }


# =============================================================================
# Main service class
# =============================================================================

class TMOReportService:
    """
    Adapter between the report engine and the live TMO dashboard service.

    filters = {
        'from_date':  'YYYY-MM-DD',
        'to_date':    'YYYY-MM-DD',
        'feeder_ids': [uuid, ...],   # used only by legacy sections
    }
    """

    def __init__(self, filters):
        self.from_date  = _parse_date(filters.get('from_date'))
        self.to_date    = _parse_date(filters.get('to_date'))
        self.feeder_ids = filters.get('feeder_ids') or []
        self._svc       = None   # lazy-init

    def _tmo(self):
        if self._svc is None:
            from tmo.services import TMOService
            self._svc = TMOService(self.from_date, self.to_date, filters={})
        return self._svc

    # ── Dashboard sections ────────────────────────────────────────────────────

    def section_tmo_overview(self):
        # get_overview() uses TMOFeederTarget (old model, no current data).
        # Pull from get_daily_energy() + get_supply_compliance() which use the
        # live TMONetworkConfig / technical_hourlyload data sources.
        energy = self._tmo().get_daily_energy()
        supply_data = self._tmo().get_supply_compliance()
        summary = supply_data.get('summary', {})
        return {
            'period':              energy.get('period', {}),
            'monthly_target_gwh':  energy.get('monthly_target_gwh', 0),
            'total_actual_gwh':    energy.get('total_actual_gwh', 0),
            'mtd_achievement_pct': energy.get('mtd_achievement_pct', 0),
            'mtd_status':          energy.get('mtd_status', 'critical'),
            'supply': {
                'compliance_pct':    summary.get('compliance_rate_pct', 0),
                'compliant_feeders': summary.get('compliant_feeders', 0),
                'total_feeders':     summary.get('total_feeders', 0),
            },
        }

    def section_tmo_daily_network_energy(self):
        return self._tmo().get_daily_energy()

    def section_tmo_daily_energy_consumed(self):
        # Same data source as daily_network_energy; frontend renders different view
        return self._tmo().get_daily_energy()

    def section_tmo_daily_allocation(self):
        return self._tmo().get_daily_allocation()

    def section_tmo_feeder_compliance_table(self):
        # MTD compliance: full period from month start to yesterday (or to_date for past months).
        # Using full period gives average daily compliance — not a single snapshot day.
        from datetime import date as _date, timedelta
        today     = _date.today()
        mtd_from  = self.from_date.replace(day=1)
        mtd_to    = self.to_date if self.to_date < today else today - timedelta(days=1)
        from tmo.services import TMOService
        svc = TMOService(mtd_from, mtd_to, filters={})
        return svc.get_supply_compliance()

    def section_tmo_compliance_by_segment(self):
        # MTD compliance by segment — same full-period logic as feeder table.
        from datetime import date as _date, timedelta
        today     = _date.today()
        mtd_from  = self.from_date.replace(day=1)
        mtd_to    = self.to_date if self.to_date < today else today - timedelta(days=1)
        from tmo.services import TMOService
        svc = TMOService(mtd_from, mtd_to, filters={})
        return svc.get_compliance_summary()

    def section_tmo_minigrids_daily(self, config=None):
        # MTD: month start → yesterday (cap at yesterday if to_date is today or future)
        from datetime import date as _date, timedelta
        today      = _date.today()
        mtd_from   = self.from_date.replace(day=1)
        mtd_to     = self.to_date if self.to_date < today else today - timedelta(days=1)
        from tmo.services import TMOService
        svc = TMOService(mtd_from, mtd_to, filters={})
        c = config or {}
        return svc.get_minigrids_daily(
            feeder_slug=c.get('feeder_slug') or None,
            q=c.get('q') or None,
            band=c.get('band') or None,
            segment=c.get('segment') or None,
        )

    def section_tmo_pear(self):
        return self._tmo().get_pear()

    def section_tmo_energy_pnl_donut(self):
        from datetime import date as _date, timedelta
        from tmo.services import TMOService
        today    = _date.today()
        yest     = self.to_date if self.to_date < today else today - timedelta(days=1)
        mtd_from = self.from_date.replace(day=1)

        def _fmt(raw):
            segs      = raw.get('segments', [])
            total_mwh = raw.get('total_actual_mwh', 0) or 1
            total_gwh = round(total_mwh / 1000, 4)
            out = []
            for s in segs:
                mwh = float(s.get('actual_mwh', 0) or 0)
                out.append({
                    'segment':    s.get('segment'),
                    'energy_gwh': round(mwh / 1000, 4),
                    'pct':        round(mwh / (total_mwh or 1) * 100, 1),
                })
            return {'segments': out, 'total_gwh': total_gwh}

        yest_data = TMOService(yest, yest, filters={}).get_energy_by_segment()
        mtd_data  = TMOService(mtd_from, yest, filters={}).get_energy_by_segment()
        return {
            'yesterday': _fmt(yest_data),
            'mtd':       _fmt(mtd_data),
        }

    def section_tmo_energy_by_voltage(self):
        return self._tmo().get_energy_by_voltage()

    def section_tmo_incidents(self):
        return self._tmo().get_incidents()

    def section_tmo_pnl_deficit(self):
        # get_pnl_targets() only covers MDI/MDNI and reads TMOMonthlySegmentTarget
        # which may have no current-month data.
        # get_gcr() covers MDI/MDNI/Regions/Total with the same keys the renderer
        # expects (consumed_gwh, gap_gwh, target_gwh) so use that instead.
        return self._tmo().get_gcr()

    def section_tmo_gcr(self):
        return self._tmo().get_gcr()

    def section_tmo_volatility(self):
        # get_volatility() uses to_date as the reference "yesterday" day.
        # If to_date is today, no energy data exists yet — use actual yesterday.
        from datetime import date as _date, timedelta
        from tmo.services import TMOService
        today    = _date.today()
        ref_date = self.to_date if self.to_date < today else today - timedelta(days=1)
        svc = TMOService(ref_date.replace(day=1), ref_date, filters={})
        return svc.get_volatility()

    # ── Legacy sections ───────────────────────────────────────────────────────

    def section_tmo_feeder_dispatch(self):
        return _legacy_feeder_dispatch(self.from_date, self.to_date, self.feeder_ids)

    def section_tmo_collection_performance(self):
        return _legacy_collection_performance(self.from_date, self.to_date)

    def section_tmo_billing_efficiency(self):
        return _legacy_billing_efficiency(self.from_date, self.to_date)

    # ── Master dispatcher ─────────────────────────────────────────────────────

    def get_all_section_data(self, section_type, config=None):
        dispatch = {
            'tmo_overview':                 self.section_tmo_overview,
            'tmo_daily_network_energy':     self.section_tmo_daily_network_energy,
            'tmo_daily_energy_consumed':    self.section_tmo_daily_energy_consumed,
            'tmo_daily_allocation':         self.section_tmo_daily_allocation,
            'tmo_feeder_compliance_table':  self.section_tmo_feeder_compliance_table,
            'tmo_compliance_by_segment':    self.section_tmo_compliance_by_segment,
            'tmo_minigrids_daily':          lambda: self.section_tmo_minigrids_daily(config),
            'tmo_pear':                     self.section_tmo_pear,
            'tmo_energy_pnl_donut':         self.section_tmo_energy_pnl_donut,
            'tmo_energy_by_voltage':        self.section_tmo_energy_by_voltage,
            'tmo_incidents':                self.section_tmo_incidents,
            'tmo_pnl_deficit':              self.section_tmo_pnl_deficit,
            'tmo_gcr':                      self.section_tmo_gcr,
            'tmo_volatility':               self.section_tmo_volatility,
            # legacy
            'tmo_feeder_dispatch':          self.section_tmo_feeder_dispatch,
            'tmo_collection_performance':   self.section_tmo_collection_performance,
            'tmo_billing_efficiency':       self.section_tmo_billing_efficiency,
        }
        method = dispatch.get(section_type)
        return method() if method else {}
