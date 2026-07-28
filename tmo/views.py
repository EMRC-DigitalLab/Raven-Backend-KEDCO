# tmo/views.py
from datetime import date, timedelta

from django.core.exceptions import ObjectDoesNotExist

from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import HasTMOAccess, HasTMOAdminAccess
from .models import (
    TMODailyAllocation,
    TMOFeederSupplyTarget,
    TMOMonthlySegmentTarget,
    TMONetworkConfig,
    TMONetworkDispatch,
    TMONetworkDispatchHourly,
    TMOSupplyHoursTarget,
)
from .services import TMOService, resolve_date_params


def _filters_from_request(request):
    p = request.query_params
    return {k: p.get(k) for k in ('segment', 'state', 'district', 'band', 'voltage', 'feeder', 'coordinate', 'region', 'status') if p.get(k)}


def _make_service(request):
    from_date, to_date = resolve_date_params(request)
    filters = _filters_from_request(request)
    return TMOService(from_date, to_date, filters)


class TMOOverviewView(APIView):
    """
    GET /api/tmo/overview/
    Top-level KPI summary: total energy dispatch achievement + supply compliance.
    Supports: ?date=, ?month=, ?from_date=&to_date=, ?state=, ?district=, ?band=, ?segment=
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_overview()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOFeederDispatchView(APIView):
    """
    GET /api/tmo/energy/dispatch/
    Per-feeder energy dispatch: target vs actual MWh, sorted by achievement % ascending (worst first).
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_feeder_dispatch()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOEnergyBySegmentView(APIView):
    """
    GET /api/tmo/energy/by-segment/
    Energy delivered grouped by MDI, MDNI, Minigrid segments.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_energy_by_segment()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOSupplyComplianceView(APIView):
    """
    GET /api/tmo/supply/compliance/
    Per-feeder hours of supply compliance against NERC Band minimums.
    Default period: current month MTD — compliance is a period metric,
    not meaningful for a single day.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            p = request.query_params
            if not any(p.get(k) for k in ('date', 'month', 'from_date', 'to_date')):
                today     = date.today()
                from_date = today.replace(day=1)
                to_date   = today - timedelta(days=1)
                if to_date < from_date:
                    to_date = from_date
                filters = _filters_from_request(request)
                service = TMOService(from_date, to_date, filters)
            else:
                service = _make_service(request)
            data = service.get_supply_compliance()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOCollectionView(APIView):
    """
    GET /api/tmo/collection/
    Collection performance: target vs actual by segment and period.
    Supports: ?segment=MDI|MDNI
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_collection()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOBillingEfficiencyView(APIView):
    """
    GET /api/tmo/billing/
    Billing efficiency % and revenue realisation % by scope.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_billing_efficiency()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOPnLTargetsView(APIView):
    """
    GET /api/tmo/pnl/
    P&L segment analysis: MDI and MDNI energy targets vs actuals,
    plus revenue and collection targets from TMOMonthlySegmentTarget.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_pnl_targets()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOMinigridsView(APIView):
    """
    GET /api/tmo/minigrids/
    Minigrid feeder performance: energy dispatch + hours of supply.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_minigrids()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOFeedersView(APIView):
    """
    GET /api/tmo/feeders/
    All onboarded feeders with energy + hours data for the selected period.
    Supports all filters: ?segment=, ?state=, ?district=, ?band=, ?voltage=
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_feeders()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMODailyEnergyView(APIView):
    """
    GET /api/tmo/energy/daily/
    Daily total network energy (GWh) for the selected period vs monthly target.
    Covers Slides 2 & 3.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_daily_energy()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMODailyEnergyBySegmentView(APIView):
    """
    GET /api/tmo/energy/daily/by-segment/
    Per-segment daily energy forecast vs actual.
    Forecast = TMOMonthlySegmentTarget.target_energy_mwh / days_in_month.
    Actual uses balloon+system fallback.
    Default: current month MTD.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            p = request.query_params
            if not any(p.get(k) for k in ('date', 'month', 'from_date', 'to_date')):
                today     = date.today()
                from_date = today.replace(day=1)
                to_date   = today - timedelta(days=1)
                if to_date < from_date:
                    to_date = from_date
                service = TMOService(from_date, to_date, _filters_from_request(request))
            else:
                service = _make_service(request)
            data = service.get_daily_energy_by_segment()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOPEARView(APIView):
    """
    GET /api/tmo/pear/
    Premium Energy Allocation Ratio: MD vs NMD share yesterday vs MTD,
    compared against configured target mix (default 65% MD / 35% NMD).
    Covers Slide 10.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_pear()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOComplianceSummaryView(APIView):
    """
    GET /api/tmo/supply/compliance/summary/
    Feeder count bucketed by compliance status (Exceeding/OnTarget/BelowTarget/Poor/Critical)
    per segment (MDI, Non-MDI Band A, Non-MDI Non-Band A).
    Default period: current month MTD.
    Covers Slide 6.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            p = request.query_params
            if not any(p.get(k) for k in ('date', 'month', 'from_date', 'to_date')):
                today     = date.today()
                from_date = today.replace(day=1)
                to_date   = today - timedelta(days=1)
                if to_date < from_date:
                    to_date = from_date
                filters = _filters_from_request(request)
                service = TMOService(from_date, to_date, filters)
            else:
                service = _make_service(request)
            data = service.get_compliance_summary()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOEnergyByVoltageView(APIView):
    """
    GET /api/tmo/energy/by-voltage/
    Per-segment daily energy split by 33KV vs 11KV, plus current vs previous month totals.
    Covers Slides 13, 14, 15.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_energy_by_voltage()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOIncidentsView(APIView):
    """
    GET /api/tmo/incidents/
    Techno-Commercial Incidence report: faults per feeder with financial loss,
    status (Rectified/Lingering) and rectification rate.
    Covers Slide 16.
    Default period: current month MTD (incidents are episodic, not daily).
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            p = request.query_params
            # If no explicit date params, default to current-month MTD instead of T-1.
            if not any(p.get(k) for k in ('date', 'month', 'from_date', 'to_date')):
                today = date.today()
                from_date = today.replace(day=1)
                to_date   = today - timedelta(days=1)
                if to_date < from_date:
                    to_date = from_date
                filters = _filters_from_request(request)
                service = TMOService(from_date, to_date, filters)
            else:
                service = _make_service(request)
            data = service.get_incidents()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOGCRView(APIView):
    """
    GET /api/tmo/gcr/
    Energy Gap-to-Cost Ratio: target vs consumed GWh per segment,
    with expected bill value, MTD bill value, and gap.
    Covers Slide 18.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_gcr()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOVolatilityView(APIView):
    """
    GET /api/tmo/volatility/
    P&L Mix Volatility Index: each segment's share of total energy for
    the selected day vs month-to-date, with Decline/Growth/Stable remark.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_volatility()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOMonitoredFeedersView(APIView):
    """
    GET /api/tmo/feeders/monitored/
    Newly commissioned feeders currently under active monitoring
    (Feeder.monitoring_end_date >= today).
    Returns per-feeder daily MWh from onboarded_at to today.
    Admin sets monitoring_end_date when commissioning a feeder.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            data = _make_service(request).get_monitored_feeders()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOMinigridsSSFView(APIView):
    """
    GET /api/tmo/minigrids/daily/
    Per-feeder daily MWh chart.
    Optional params: feeder_slug, q (name search), band (slug), segment (pl_segment).
    Default (no feeder params): minigrid feeders only (SSF behaviour).
    - feeders[]: per-feeder daily MWh array → one bar chart each
    - summary: all matched feeders combined per day + grand total
    Default period: current month MTD.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            p = request.query_params
            if not any(p.get(k) for k in ('date', 'month', 'from_date', 'to_date')):
                today     = date.today()
                from_date = today.replace(day=1)
                to_date   = today - timedelta(days=1)
                if to_date < from_date:
                    to_date = from_date
                service = TMOService(from_date, to_date, _filters_from_request(request))
            else:
                service = _make_service(request)
            data = service.get_minigrids_daily(
                feeder_slug=p.get('feeder_slug') or None,
                q=p.get('q') or None,
                band=p.get('band') or None,
                segment=p.get('segment') or None,
            )
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMODailyAllocationView(APIView):
    """
    GET /api/tmo/allocation/daily/
    Per-day: TCN expected allocation (MW) vs actual avg consumption (MW) vs unpicked gap.
    Default: current month MTD.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            p = request.query_params
            if not any(p.get(k) for k in ('date', 'month', 'from_date', 'to_date')):
                today     = date.today()
                from_date = today.replace(day=1)
                to_date   = today - timedelta(days=1)
                if to_date < from_date:
                    to_date = from_date
                service = TMOService(from_date, to_date, _filters_from_request(request))
            else:
                service = _make_service(request)
            data = service.get_daily_allocation()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOFeederDetailView(APIView):
    """
    GET /api/tmo/feeders/<feeder_slug>/
    Daily breakdown for a single feeder over the selected period.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request, feeder_slug):
        try:
            data = _make_service(request).get_feeder_detail(feeder_slug)
            return Response(data)
        except ObjectDoesNotExist:
            return Response({'error': 'Feeder not found or not onboarded.'}, status=404)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMONetworkDispatchView(APIView):
    """
    GET /api/tmo/network/dispatch/
    Daily 33KV dispatch reconciliation: KEDCO allocation vs DISCO offtake.
    Supports: ?month=YYYY-MM  |  ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
    Default: current month MTD.

    Response:
      {
        "period": {"from": "...", "to": "..."},
        "summary": {"avg_kedco_mw": ..., "avg_disco_mw": ..., "avg_variance_mw": ...,
                    "green_days": ..., "red_days": ...},
        "days": [
          {"date": "2026-06-01", "kedco_allocation_mw": 170.46, "disco_offtake_mw": 186.20,
           "variance_mw": -15.74, "available_generation_mw": 0, "status": "RED",
           "has_hourly": true},
          ...
        ]
      }
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        try:
            from_date, to_date = resolve_date_params(request)
        except Exception:
            today     = date.today()
            from_date = today.replace(day=1)
            to_date   = today - timedelta(days=1)
            if to_date < from_date:
                to_date = from_date

        qs = TMONetworkDispatch.objects.filter(
            date__gte=from_date, date__lte=to_date
        ).order_by('date')

        days = []
        for obj in qs:
            var = float(obj.variance_mw)
            days.append({
                'date':                   str(obj.date),
                'kedco_allocation_mw':    float(obj.kedco_allocation_mw),
                'disco_offtake_mw':       float(obj.disco_offtake_mw),
                'variance_mw':            round(var, 4),
                'available_generation_mw': float(obj.available_generation_mw),
                'status':                 'GREEN' if var >= 0 else 'RED',
                'has_hourly':             True,
            })

        n = len(days)
        summary = {}
        if n:
            summary = {
                'avg_kedco_mw':    round(sum(d['kedco_allocation_mw'] for d in days) / n, 2),
                'avg_disco_mw':    round(sum(d['disco_offtake_mw']    for d in days) / n, 2),
                'avg_variance_mw': round(sum(d['variance_mw']         for d in days) / n, 2),
                'green_days':      sum(1 for d in days if d['status'] == 'GREEN'),
                'red_days':        sum(1 for d in days if d['status'] == 'RED'),
            }

        return Response({
            'period':  {'from': str(from_date), 'to': str(to_date)},
            'summary': summary,
            'days':    days,
        })


class TMONetworkDispatchHourlyView(APIView):
    """
    GET /api/tmo/network/dispatch/hourly/?date=YYYY-MM-DD
    Returns the 24 hourly MW readings for a single day.

    Response:
      {
        "date": "2026-06-01",
        "daily_summary": {"kedco_allocation_mw": ..., "disco_offtake_mw": ..., "variance_mw": ..., "status": "RED"},
        "hours": [
          {"hour": 1, "kedco_allocation_mw": ..., "disco_offtake_mw": ..., "variance_mw": ..., "status": "RED"},
          ...
        ]
      }
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        date_str = request.query_params.get('date')
        if not date_str:
            return Response({'error': 'date param required (YYYY-MM-DD)'}, status=400)
        try:
            query_date = date.fromisoformat(date_str)
        except ValueError:
            return Response({'error': 'Invalid date format — use YYYY-MM-DD'}, status=400)

        try:
            daily = TMONetworkDispatch.objects.get(date=query_date)
        except TMONetworkDispatch.DoesNotExist:
            return Response({'error': f'No dispatch data for {date_str}'}, status=404)

        hourly_qs = TMONetworkDispatchHourly.objects.filter(date=query_date).order_by('hour')

        hours = []
        for h in hourly_qs:
            var = float(h.variance_mw) if h.variance_mw is not None else None
            hours.append({
                'hour':                   h.hour,
                'kedco_allocation_mw':    float(h.kedco_allocation_mw) if h.kedco_allocation_mw is not None else None,
                'disco_offtake_mw':       float(h.disco_offtake_mw)    if h.disco_offtake_mw    is not None else None,
                'variance_mw':            round(var, 4) if var is not None else None,
                'available_generation_mw': float(h.available_generation_mw) if h.available_generation_mw is not None else None,
                'status':                 ('GREEN' if var >= 0 else 'RED') if var is not None else None,
            })

        var_d = float(daily.variance_mw)
        return Response({
            'date': str(query_date),
            'daily_summary': {
                'kedco_allocation_mw':    float(daily.kedco_allocation_mw),
                'disco_offtake_mw':       float(daily.disco_offtake_mw),
                'variance_mw':            round(var_d, 4),
                'available_generation_mw': float(daily.available_generation_mw),
                'status':                 'GREEN' if var_d >= 0 else 'RED',
            },
            'hours': hours,
        })


# ── Settings views ────────────────────────────────────────────────────────────

class TMOFeederTargetUploadView(APIView):
    """
    GET    /api/tmo/settings/feeder-targets/?month=2026-07
           Returns all per-feeder supply hours targets set for the month.

    POST   /api/tmo/settings/feeder-targets/
           Upload an Excel file (multipart/form-data, field name "file") with columns:
             A: Feeder name  (e.g. "33KV JODA" or "11KV KOFAR NASSARAWA")
             B: Target hours (daily hours-of-supply target, e.g. 22.37)
           Body params: month=2026-07

    DELETE /api/tmo/settings/feeder-targets/?month=2026-07
           Removes all per-feeder targets for the month → reverts to segment defaults.
    """
    permission_classes = [HasTMOAccess]

    # Known name differences between the Excel and the DB feeder names
    _ALIASES = {
        'NB CEREMIC':           'NB CERAMIC',
        'RUMMAWA':              'RUMAWA',
        'CHALLAWA WATER PLANT': 'CHALLAWA WATER WORKS',
        'BATA':                 'SHARADA BATA',
    }

    @staticmethod
    def _normalise(name: str) -> str:
        import re
        return re.sub(r'\s+', ' ', name.strip().upper())

    def _strip_prefix(self, name: str) -> str:
        name = self._normalise(name)
        for prefix in ('33KV ', '11KV ', '33 KV ', '11 KV '):
            if name.startswith(prefix):
                return name[len(prefix):].strip()
        return name

    def get(self, request):
        month_str = request.query_params.get('month')
        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                return Response({'error': 'month must be YYYY-MM'}, status=400)
        else:
            today = date.today()
            year, month = today.year, today.month

        rows = []
        for t in TMOFeederSupplyTarget.objects.filter(year=year, month=month).select_related('feeder'):
            rows.append({
                'feeder_id':    str(t.feeder_id),
                'feeder_name':  t.feeder.name,
                'voltage_level': t.feeder.voltage_level,
                'target_hours': float(t.target_hours),
                'updated_at':   t.updated_at.isoformat(),
            })

        return Response({'year': year, 'month': month, 'count': len(rows), 'feeders': rows})

    def post(self, request):
        import io
        import openpyxl
        from common.models import Feeder

        month_str = request.data.get('month') or request.query_params.get('month')
        if not month_str:
            return Response({'error': 'month is required (e.g. 2026-07)'}, status=400)
        try:
            year, month = map(int, month_str.split('-'))
        except (ValueError, AttributeError):
            return Response({'error': 'month must be YYYY-MM'}, status=400)

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({'error': '"file" is required (Excel upload)'}, status=400)

        try:
            wb = openpyxl.load_workbook(io.BytesIO(uploaded_file.read()), data_only=True)
        except Exception as exc:
            return Response({'error': f'Could not read Excel file: {exc}'}, status=400)

        ws = wb.active

        # Build feeder lookup: normalised-name → feeder object
        feeder_lookup = {}
        for f in Feeder.objects.all():
            feeder_lookup[self._normalise(f.name)] = f

        matched, unmatched, errors = [], [], []

        for row in ws.iter_rows(min_row=2, values_only=True):
            raw_name = row[0]
            raw_target = row[1]
            if not raw_name or raw_target is None:
                continue

            stripped = self._strip_prefix(str(raw_name))

            # 1. Exact match (normalised)
            feeder = feeder_lookup.get(stripped)
            # 2. Alias map (known Excel ↔ DB name differences)
            if feeder is None:
                feeder = feeder_lookup.get(self._ALIASES.get(stripped, ''))
            # 3. Strip AUTORECLOSER / RECLOSER suffixes
            if feeder is None:
                for suffix in (' AUTORECLOSER', ' AUTO RECLOSER', ' RECLOSER', ' AR'):
                    if stripped.endswith(suffix):
                        feeder = feeder_lookup.get(stripped[:-len(suffix)].strip())
                        if feeder:
                            break

            if feeder is None:
                unmatched.append(str(raw_name))
                continue

            try:
                target_hours = float(raw_target)
                if target_hours < 0 or target_hours > 24:
                    errors.append(f'{raw_name}: target {raw_target} out of range (0–24)')
                    continue
            except (TypeError, ValueError):
                errors.append(f'{raw_name}: invalid target "{raw_target}"')
                continue

            TMOFeederSupplyTarget.objects.update_or_create(
                feeder=feeder, year=year, month=month,
                defaults={'target_hours': round(target_hours, 2)},
            )
            matched.append({'feeder': feeder.name, 'target_hours': target_hours})

        return Response({
            'year':      year,
            'month':     month,
            'matched':   len(matched),
            'unmatched': unmatched,
            'errors':    errors,
        }, status=200 if matched else 400)

    def delete(self, request):
        month_str = request.query_params.get('month')
        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                return Response({'error': 'month must be YYYY-MM'}, status=400)
        else:
            today = date.today()
            year, month = today.year, today.month

        deleted, _ = TMOFeederSupplyTarget.objects.filter(year=year, month=month).delete()
        return Response({
            'year':    year,
            'month':   month,
            'deleted': deleted,
            'message': f'Removed {deleted} feeder targets for {year}-{month:02d} — reverted to segment defaults.',
        })


class TMOSegmentTargetsView(APIView):
    """
    GET  /api/tmo/settings/segment-targets/?month=2026-07
         Returns current targets for all 3 segments for that month.
    POST /api/tmo/settings/segment-targets/
         Upsert targets for one or more segments (admin/manager only).
    """
    permission_classes = [HasTMOAccess]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasTMOAdminAccess()]
        return [HasTMOAccess()]

    def get(self, request):
        month_str = request.query_params.get('month')
        if not month_str:
            from datetime import date
            today = date.today()
            year, month = today.year, today.month
        else:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                return Response({'error': 'month must be YYYY-MM'}, status=400)

        rows = TMOMonthlySegmentTarget.objects.filter(year=year, month=month)
        data = {
            'year': year, 'month': month,
            'segments': [
                {
                    'segment':               r.segment,
                    'target_energy_mwh':     float(r.target_energy_mwh),
                    'average_tariff_per_kwh': float(r.average_tariff_per_kwh),
                    'target_revenue_ngn':    float(r.target_revenue_ngn),
                    'target_collection_ngn': float(r.target_collection_ngn),
                    'updated_at':            r.updated_at.isoformat(),
                }
                for r in rows
            ],
        }
        return Response(data)

    def post(self, request):
        """
        Body:
        {
          "year": 2026, "month": 7,
          "segments": [
            { "segment": "MDI",     "target_energy_mwh": 64000, "average_tariff_per_kwh": 225 },
            { "segment": "MDNI",    "target_energy_mwh": 23500, "average_tariff_per_kwh": 197 },
            { "segment": "Regions", "target_energy_mwh": 61200, "average_tariff_per_kwh": 52  }
          ]
        }
        Optional extra fields per segment: target_revenue_ngn, target_collection_ngn
        """
        body = request.data
        year  = body.get('year')
        month = body.get('month')
        segs  = body.get('segments', [])

        if not year or not month or not segs:
            return Response({'error': 'year, month and segments are required'}, status=400)

        VALID_SEGMENTS = {'MDI', 'MDNI', 'Regions'}
        updated = []
        for s in segs:
            seg = s.get('segment')
            if seg not in VALID_SEGMENTS:
                return Response({'error': f'Invalid segment: {seg}. Must be MDI, MDNI or Regions'}, status=400)

            defaults = {}
            if 'target_energy_mwh'     in s: defaults['target_energy_mwh']     = s['target_energy_mwh']
            if 'average_tariff_per_kwh' in s: defaults['average_tariff_per_kwh'] = s['average_tariff_per_kwh']
            if 'target_revenue_ngn'    in s: defaults['target_revenue_ngn']    = s['target_revenue_ngn']
            if 'target_collection_ngn' in s: defaults['target_collection_ngn'] = s['target_collection_ngn']

            obj, _ = TMOMonthlySegmentTarget.objects.update_or_create(
                segment=seg, year=year, month=month,
                defaults=defaults,
            )
            updated.append({
                'segment':               obj.segment,
                'target_energy_mwh':     float(obj.target_energy_mwh),
                'average_tariff_per_kwh': float(obj.average_tariff_per_kwh),
                'target_revenue_ngn':    float(obj.target_revenue_ngn),
                'target_collection_ngn': float(obj.target_collection_ngn),
            })

        return Response({'year': year, 'month': month, 'updated': updated})


class TMONetworkConfigView(APIView):
    """
    GET  /api/tmo/settings/network-config/?month=2026-07
    POST /api/tmo/settings/network-config/
         Set total monthly energy target (GWh) and MD share target (%) (admin/manager only).
    """
    permission_classes = [HasTMOAccess]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasTMOAdminAccess()]
        return [HasTMOAccess()]

    def get(self, request):
        month_str = request.query_params.get('month')
        if not month_str:
            from datetime import date
            today = date.today()
            year, month = today.year, today.month
        else:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                return Response({'error': 'month must be YYYY-MM'}, status=400)

        obj = TMONetworkConfig.objects.filter(year=year, month=month).first()
        if not obj:
            return Response({
                'year': year, 'month': month,
                'monthly_energy_target_gwh': 0.0,
                'target_md_share_pct': 65.0,
                'configured': False,
            })
        return Response({
            'year': year, 'month': month,
            'monthly_energy_target_gwh': float(obj.monthly_energy_target_gwh),
            'target_md_share_pct':       float(obj.target_md_share_pct),
            'updated_at':                obj.updated_at.isoformat(),
            'configured': True,
        })

    def post(self, request):
        """
        Body:
        {
          "year": 2026, "month": 7,
          "monthly_energy_target_gwh": 148.7,
          "target_md_share_pct": 55.0
        }
        """
        body  = request.data
        year  = body.get('year')
        month = body.get('month')
        if not year or not month:
            return Response({'error': 'year and month are required'}, status=400)

        defaults = {}
        if 'monthly_energy_target_gwh' in body: defaults['monthly_energy_target_gwh'] = body['monthly_energy_target_gwh']
        if 'target_md_share_pct'       in body: defaults['target_md_share_pct']       = body['target_md_share_pct']

        obj, _ = TMONetworkConfig.objects.update_or_create(
            year=year, month=month, defaults=defaults,
        )
        return Response({
            'year':  obj.year,
            'month': obj.month,
            'monthly_energy_target_gwh': float(obj.monthly_energy_target_gwh),
            'target_md_share_pct':       float(obj.target_md_share_pct),
        })


class TMODailyForecastView(APIView):
    """
    GET  /api/tmo/settings/daily-forecast/?month=2026-07
         Returns per-day energy forecast for every day of the month.
         If a day has a TMODailyAllocation row (set manually or imported), its value is used.
         Otherwise the day shows the flat fallback: monthly_target_gwh / days_in_month.

    POST /api/tmo/settings/daily-forecast/
         Bulk-set per-day forecast values for a month (any TMO user).
         Body:
         {
           "year": 2026, "month": 7,
           "days": [
             {"day": 1, "forecast_gwh": 6.20},
             {"day": 2, "forecast_gwh": 5.80}
           ]
         }
         Omitted days are left unchanged. To reset a day to the flat default,
         pass "forecast_gwh": null — this removes its TMODailyAllocation row.

    DELETE /api/tmo/settings/daily-forecast/?month=2026-07
           Removes all manual TMODailyAllocation rows for the month,
           reverting every day back to the flat monthly default.
    """
    permission_classes = [HasTMOAccess]

    def get(self, request):
        import calendar
        from datetime import date as _date

        month_str = request.query_params.get('month')
        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                return Response({'error': 'month must be YYYY-MM'}, status=400)
        else:
            today = date.today()
            year, month = today.year, today.month

        days_in_month = calendar.monthrange(year, month)[1]

        config = TMONetworkConfig.objects.filter(year=year, month=month).first()
        monthly_target_gwh = float(config.monthly_energy_target_gwh) if config else 0.0
        flat_daily_gwh = round(monthly_target_gwh / days_in_month, 4) if days_in_month else 0.0

        # Load all allocation rows for this month into a day→row map
        month_start = _date(year, month, 1)
        month_end   = _date(year, month, days_in_month)
        alloc_rows  = {
            a.date.day: a
            for a in TMODailyAllocation.objects.filter(
                date__gte=month_start, date__lte=month_end
            )
        }

        from tmo.constants import STANDARD_DAILY_FORECAST_GWH

        days = []
        for day in range(1, days_in_month + 1):
            d = _date(year, month, day)
            row = alloc_rows.get(day)
            if row:
                forecast_gwh = round(float(row.expected_mw) * 24.0 / 1000.0, 4)
                source = row.source  # 'manual' or 'datanest'
            else:
                # Standard pattern is the default; flat monthly is last resort
                forecast_gwh = STANDARD_DAILY_FORECAST_GWH.get(day, flat_daily_gwh)
                source = 'standard_default'
            days.append({
                'day':          day,
                'date':         str(d),
                'forecast_gwh': forecast_gwh,
                'source':       source,
            })

        return Response({
            'year':               year,
            'month':              month,
            'days_in_month':      days_in_month,
            'monthly_target_gwh': round(monthly_target_gwh, 4),
            'flat_daily_gwh':     flat_daily_gwh,
            'days':               days,
        })

    def post(self, request):
        import calendar
        from datetime import date as _date

        body  = request.data
        year  = body.get('year')
        month = body.get('month')
        if not year or not month:
            return Response({'error': 'year and month are required'}, status=400)
        try:
            year, month = int(year), int(month)
            days_in_month = calendar.monthrange(year, month)[1]
        except (ValueError, TypeError):
            return Response({'error': 'year and month must be integers'}, status=400)

        days_input = body.get('days')
        if not isinstance(days_input, list):
            return Response({'error': '"days" must be a list'}, status=400)

        updated, reset = [], []
        for entry in days_input:
            day = entry.get('day')
            forecast_gwh = entry.get('forecast_gwh')
            if not isinstance(day, int) or day < 1 or day > days_in_month:
                continue
            target_date = _date(year, month, day)

            if forecast_gwh is None:
                # Reset this day → remove allocation row so flat default kicks in
                deleted, _ = TMODailyAllocation.objects.filter(
                    date=target_date, source='manual'
                ).delete()
                if deleted:
                    reset.append(day)
            else:
                try:
                    gwh = float(forecast_gwh)
                    if gwh < 0:
                        continue
                except (TypeError, ValueError):
                    continue
                expected_mw = round(gwh * 1000.0 / 24.0, 2)
                TMODailyAllocation.objects.update_or_create(
                    date=target_date,
                    defaults={'expected_mw': expected_mw, 'source': 'manual'},
                )
                updated.append(day)

        return Response({
            'year':    year,
            'month':   month,
            'updated': sorted(updated),
            'reset':   sorted(reset),
        })

    def delete(self, request):
        import calendar
        from datetime import date as _date

        month_str = request.query_params.get('month')
        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                return Response({'error': 'month must be YYYY-MM'}, status=400)
        else:
            today = date.today()
            year, month = today.year, today.month

        days_in_month = calendar.monthrange(year, month)[1]
        month_start = _date(year, month, 1)
        month_end   = _date(year, month, days_in_month)

        deleted, _ = TMODailyAllocation.objects.filter(
            date__gte=month_start, date__lte=month_end, source='manual'
        ).delete()

        return Response({
            'year':    year,
            'month':   month,
            'deleted': deleted,
            'message': f'Reset {deleted} manual forecast rows — all days now use flat monthly default.',
        })


class TMOSupplyHoursTargetView(APIView):
    """
    GET  /api/tmo/settings/supply-hours/?month=2026-07
    POST /api/tmo/settings/supply-hours/
         Set monthly supply hours target per DM segment (admin/manager only).
    """
    permission_classes = [HasTMOAccess]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [HasTMOAdminAccess()]
        return [HasTMOAccess()]

    VALID_SEGMENTS = {'MDI', 'Non-MDI Band A', 'Non-MDI, Non-Band A'}

    def get(self, request):
        month_str = request.query_params.get('month')
        if not month_str:
            from datetime import date
            today = date.today()
            year, month = today.year, today.month
        else:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                return Response({'error': 'month must be YYYY-MM'}, status=400)

        rows = TMOSupplyHoursTarget.objects.filter(year=year, month=month)
        return Response({
            'year': year, 'month': month,
            'segments': [
                {
                    'segment':      r.segment,
                    'target_hours': float(r.target_hours),
                    'updated_at':   r.updated_at.isoformat(),
                }
                for r in rows
            ],
        })

    def post(self, request):
        """
        Body:
        {
          "year": 2026, "month": 7,
          "segments": [
            { "segment": "MDI",                  "target_hours": 20 },
            { "segment": "Non-MDI Band A",        "target_hours": 16 },
            { "segment": "Non-MDI, Non-Band A",   "target_hours": 12 }
          ]
        }
        """
        body  = request.data
        year  = body.get('year')
        month = body.get('month')
        segs  = body.get('segments', [])

        if not year or not month or not segs:
            return Response({'error': 'year, month and segments are required'}, status=400)

        updated = []
        for s in segs:
            seg   = s.get('segment')
            hours = s.get('target_hours')
            if seg not in self.VALID_SEGMENTS:
                return Response({
                    'error': f'Invalid segment: {seg}. Must be one of: MDI, Non-MDI Band A, Non-MDI, Non-Band A'
                }, status=400)
            if hours is None:
                return Response({'error': f'target_hours required for {seg}'}, status=400)

            obj, _ = TMOSupplyHoursTarget.objects.update_or_create(
                segment=seg, year=year, month=month,
                defaults={'target_hours': hours},
            )
            updated.append({'segment': obj.segment, 'target_hours': float(obj.target_hours)})

        return Response({'year': year, 'month': month, 'updated': updated})


class GoogleSheetFeedView(APIView):
    """
    Manage monthly Google Sheet feed URLs for the live data pipeline.
    Requires technical module access.

    GET  /api/tmo/sheet-feeds/      — list all registered feeds
    POST /api/tmo/sheet-feeds/      — register/update a monthly URL
                                      body: {feed_type, year, month, url}
    POST /api/tmo/sheet-feeds/      — trigger manual sync
                                      body: {action:'sync', feed_type, year, month}
    """
    VALID_FEED_TYPES = ('33kv_load_flow', '33kv_energy_accounting', '11kv_load_flow', '11kv_energy_accounting')

    def get_permissions(self):
        from technical.permissions import HasTechnicalAccess
        return [HasTechnicalAccess()]

    def get(self, request):
        from tmo.models import GoogleSheetFeed
        feeds = GoogleSheetFeed.objects.all().order_by('-year', '-month', 'feed_type')
        return Response([
            {
                'id':              f.id,
                'feed_type':       f.feed_type,
                'feed_type_label': f.get_feed_type_display(),
                'year':            f.year,
                'month':           f.month,
                'spreadsheet_id':  f.spreadsheet_id,
                'spreadsheet_url': f.spreadsheet_url,
                'is_active':       f.is_active,
                'last_synced_at':  f.last_synced_at,
                'last_sync_log':   f.last_sync_log,
            }
            for f in feeds
        ])

    def post(self, request):
        from datetime import date as _date
        from tmo.models import GoogleSheetFeed

        # ── Manual sync trigger ──────────────────────────────────────────────
        if request.data.get('action') == 'sync':
            feed_type = request.data.get('feed_type', '33kv_load_flow')
            today     = _date.today()
            year      = int(request.data.get('year',  today.year))
            month_val = int(request.data.get('month', today.month))

            feed = GoogleSheetFeed.objects.filter(
                feed_type=feed_type, year=year, month=month_val, is_active=True
            ).first()
            if not feed:
                return Response(
                    {'error': f'No active {feed_type} feed for {year}-{month_val:02d}'},
                    status=404
                )
            from tmo.tasks import sync_33kv_sheet_task
            sync_33kv_sheet_task.delay()
            return Response({'status': 'queued', 'feed': str(feed)})

        # ── Register / update feed URL ────────────────────────────────────────
        feed_type = request.data.get('feed_type')
        year      = request.data.get('year')
        month_val = request.data.get('month')
        url       = request.data.get('url')

        if not all([feed_type, year, month_val, url]):
            return Response(
                {'error': 'feed_type, year, month and url are required'},
                status=400
            )
        if feed_type not in self.VALID_FEED_TYPES:
            return Response({
                'error': f'feed_type must be one of: {", ".join(self.VALID_FEED_TYPES)}'
            }, status=400)

        spreadsheet_id = GoogleSheetFeed.extract_spreadsheet_id(url)
        if not spreadsheet_id:
            return Response(
                {'error': 'Could not extract spreadsheet ID from URL'}, status=400
            )

        feed, created = GoogleSheetFeed.objects.update_or_create(
            feed_type=feed_type, year=int(year), month=int(month_val),
            defaults={
                'spreadsheet_id':  spreadsheet_id,
                'spreadsheet_url': url,
                'is_active':       True,
            }
        )
        return Response({
            'status':         'created' if created else 'updated',
            'feed':           str(feed),
            'feed_type':      feed.feed_type,
            'feed_type_label': feed.get_feed_type_display(),
            'year':           feed.year,
            'month':          feed.month,
            'spreadsheet_id': feed.spreadsheet_id,
        }, status=201 if created else 200)


class SheetAlertEmailView(APIView):
    """
    Manage alert email recipients for Google Sheet feed notifications.
    Requires technical module access.

    GET    /api/tmo/sheet-feeds/alert-emails/   — list active recipients
    POST   body: {email, name}                  — add/re-activate a recipient
    DELETE body: {email}                        — remove a recipient
    """
    def get_permissions(self):
        from technical.permissions import HasTechnicalAccess
        return [HasTechnicalAccess()]

    def get(self, request):
        from tmo.models import SheetAlertEmail
        return Response([
            {'id': e.id, 'email': e.email, 'name': e.name, 'is_active': e.is_active}
            for e in SheetAlertEmail.objects.all()
        ])

    def post(self, request):
        from tmo.models import SheetAlertEmail
        email = (request.data.get('email') or '').strip().lower()
        name  = (request.data.get('name') or '').strip()
        if not email:
            return Response({'error': 'email is required'}, status=400)

        obj, created = SheetAlertEmail.objects.update_or_create(
            email=email,
            defaults={'name': name, 'is_active': True},
        )
        return Response(
            {'status': 'added' if created else 'updated', 'email': obj.email, 'name': obj.name},
            status=201 if created else 200,
        )

    def delete(self, request):
        from tmo.models import SheetAlertEmail
        email = (request.data.get('email') or '').strip().lower()
        if not email:
            return Response({'error': 'email is required'}, status=400)
        deleted, _ = SheetAlertEmail.objects.filter(email=email).delete()
        if deleted:
            return Response({'status': 'deleted', 'email': email})
        return Response({'error': 'Email not found'}, status=404)
