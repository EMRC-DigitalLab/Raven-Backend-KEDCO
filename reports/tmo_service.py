"""
reports/tmo_service.py

TMO (Technical & Management Operations) Report Data Service.

Reads from Raven's synced TMO models (PostgreSQL) — populated every 30 min
by the Celery TMO sync tasks from DataNest.

For tmo_feeder_dispatch, actual MWh is cross-referenced against Raven's
EnergyDelivered table (feeder meter data).
"""

from datetime import datetime

from django.db.models import Sum

from commercial.models import TMOBillingEfficiency, TMOCollectionTarget, TMOFeederTarget
from technical.models import EnergyDelivered


def _parse_date(date_val):
    if isinstance(date_val, str):
        return datetime.strptime(date_val.split('T')[0].strip(), '%Y-%m-%d').date()
    if isinstance(date_val, datetime):
        return date_val.date()
    return date_val


def _pct(numerator, denominator):
    try:
        return round(float(numerator) / float(denominator) * 100, 2)
    except (ZeroDivisionError, TypeError):
        return 0.0


def _var_pct(actual, target):
    try:
        return round((float(actual) - float(target)) / float(target) * 100, 2)
    except (ZeroDivisionError, TypeError):
        return 0.0


def _compliance(achievement_pct):
    if achievement_pct >= 100: return 'on_target'
    if achievement_pct >= 90:  return 'below_target'
    if achievement_pct >= 75:  return 'poor'
    return 'critical'


class TMOReportService:
    """
    Fetches data for TMO report sections from Raven's synced TMO models.

    filters = {
        'from_date'  : 'YYYY-MM-DD',
        'to_date'    : 'YYYY-MM-DD',
        'feeder_ids' : [uuid, ...],   # optional
    }
    """

    def __init__(self, filters):
        self.from_date  = _parse_date(filters.get('from_date'))
        self.to_date    = _parse_date(filters.get('to_date'))
        self.feeder_ids = filters.get('feeder_ids') or []

    # ── 1. Feeder Dispatch Targets ─────────────────────────────────────────────

    def section_tmo_feeder_dispatch(self):
        """
        TMOFeederTarget (targets) vs Raven EnergyDelivered (actuals).
        """
        targets_qs = TMOFeederTarget.objects.filter(
            target_date__gte=self.from_date,
            target_date__lte=self.to_date,
        )
        if self.feeder_ids:
            targets_qs = targets_qs.filter(feeder_id__in=self.feeder_ids)

        targets_by_code = {}
        for row in targets_qs.values('feeder_code', 'feeder__name', 'feeder_id').annotate(
            total_target=Sum('target_mwh')
        ):
            targets_by_code[row['feeder_code']] = {
                'feeder_id':   str(row['feeder_id']) if row['feeder_id'] else row['feeder_code'],
                'feeder_name': row['feeder__name'] or row['feeder_code'],
                'target_mwh':  float(row['total_target'] or 0),
            }

        actuals_qs = EnergyDelivered.objects.filter(
            date__gte=self.from_date,
            date__lte=self.to_date,
        )
        if self.feeder_ids:
            actuals_qs = actuals_qs.filter(feeder_id__in=self.feeder_ids)

        actuals_by_slug = {
            row['feeder__slug']: float(row['total'] or 0)
            for row in actuals_qs.values('feeder__slug').annotate(total=Sum('energy_mwh'))
        }

        all_codes = set(targets_by_code) | set(actuals_by_slug)
        rows = []
        for code in sorted(all_codes):
            info       = targets_by_code.get(code, {'feeder_id': code, 'feeder_name': code, 'target_mwh': 0.0})
            target_mwh = info['target_mwh']
            actual_mwh = actuals_by_slug.get(code, 0.0)
            ach        = _pct(actual_mwh, target_mwh) if target_mwh else 0.0
            rows.append({
                'feeder_id':       info['feeder_id'],
                'feeder_name':     info['feeder_name'],
                'target_mwh':      target_mwh,
                'actual_mwh':      actual_mwh,
                'variance_mwh':    round(actual_mwh - target_mwh, 4),
                'variance_pct':    _var_pct(actual_mwh, target_mwh),
                'achievement_pct': ach,
                'status':          _compliance(ach),
            })

        total_target = sum(r['target_mwh'] for r in rows)
        total_actual = sum(r['actual_mwh'] for r in rows)

        return {
            'feeders':                 rows,
            'total_target_mwh':        round(total_target, 4),
            'total_actual_mwh':        round(total_actual, 4),
            'total_variance_mwh':      round(total_actual - total_target, 4),
            'overall_achievement_pct': _pct(total_actual, total_target),
            'overall_status':          _compliance(_pct(total_actual, total_target)),
        }

    # ── 2. Collection Performance ──────────────────────────────────────────────

    def section_tmo_collection_performance(self):
        qs = TMOCollectionTarget.objects.filter(
            period_month__gte=self.from_date,
            period_month__lte=self.to_date,
        ).order_by('period_month', 'segment_code', 'sub_segment')

        rows = []
        for obj in qs:
            target = float(obj.target_amount)
            actual = float(obj.actual_amount)
            ach    = _pct(actual, target)
            rows.append({
                'segment_code':    obj.segment_code,
                'sub_segment':     obj.sub_segment,
                'period_month':    str(obj.period_month),
                'target_amount':   target,
                'actual_amount':   actual,
                'variance':        round(actual - target, 2),
                'achievement_pct': ach,
                'status':          _compliance(ach),
            })

        total_target = sum(r['target_amount'] for r in rows)
        total_actual = sum(r['actual_amount'] for r in rows)

        return {
            'segments':                rows,
            'total_target':            round(total_target, 2),
            'total_actual':            round(total_actual, 2),
            'total_variance':          round(total_actual - total_target, 2),
            'overall_achievement_pct': _pct(total_actual, total_target),
            'overall_status':          _compliance(_pct(total_actual, total_target)),
        }

    # ── 3. Billing Efficiency ──────────────────────────────────────────────────

    def section_tmo_billing_efficiency(self):
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
                'billing_efficiency_pct': be_pct,
                'target_revenue_amount':  t_rev,
                'billed_amount':          b_rev,
                'revenue_efficiency_pct': rr_pct,
                'be_status':              _compliance(be_pct),
                'rr_status':              _compliance(rr_pct),
            })

        total_del  = sum(r['energy_delivered_gwh']  for r in rows)
        total_bil  = sum(r['energy_billed_gwh']     for r in rows)
        total_trev = sum(r['target_revenue_amount'] for r in rows)
        total_brev = sum(r['billed_amount']         for r in rows)

        return {
            'rows':                       rows,
            'total_energy_delivered_gwh': round(total_del,  4),
            'total_energy_billed_gwh':    round(total_bil,  4),
            'overall_billing_eff_pct':    _pct(total_bil, total_del),
            'total_target_revenue':       round(total_trev, 2),
            'total_billed_amount':        round(total_brev, 2),
            'overall_revenue_eff_pct':    _pct(total_brev, total_trev),
        }

    # ── Master dispatcher ──────────────────────────────────────────────────────

    def get_all_section_data(self, section_type, config=None):  # noqa: ARG002
        dispatch = {
            'tmo_feeder_dispatch':        self.section_tmo_feeder_dispatch,
            'tmo_collection_performance': self.section_tmo_collection_performance,
            'tmo_billing_efficiency':     self.section_tmo_billing_efficiency,
        }
        method = dispatch.get(section_type)
        return method() if method else {}
