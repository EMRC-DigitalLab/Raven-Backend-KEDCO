# tmo/views.py
from datetime import date, timedelta

from django.core.exceptions import ObjectDoesNotExist

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            data = _make_service(request).get_monitored_feeders()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMOMinigridsSSFView(APIView):
    """
    GET /api/tmo/minigrids/daily/
    Haske Solar Supplementation Factor (SSF):
    - feeders[]: per-minigrid daily MWh array → one bar chart each
    - summary: all minigrids combined per day + grand total → summary table
    Default: current month MTD.
    """
    permission_classes = [IsAuthenticated]

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
            data = service.get_minigrids_daily()
            return Response(data)
        except Exception as exc:
            return Response({'error': str(exc)}, status=500)


class TMODailyAllocationView(APIView):
    """
    GET /api/tmo/allocation/daily/
    Per-day: TCN expected allocation (MW) vs actual avg consumption (MW) vs unpicked gap.
    Default: current month MTD.
    """
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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

class TMOSegmentTargetsView(APIView):
    """
    GET  /api/tmo/settings/segment-targets/?month=2026-07
         Returns current targets for all 3 segments for that month.
    POST /api/tmo/settings/segment-targets/
         Upsert targets for one or more segments.
    """
    permission_classes = [IsAuthenticated]

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
         Set total monthly energy target (GWh) and MD share target (%).
    """
    permission_classes = [IsAuthenticated]

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


class TMOSupplyHoursTargetView(APIView):
    """
    GET  /api/tmo/settings/supply-hours/?month=2026-07
    POST /api/tmo/settings/supply-hours/
         Set monthly supply hours target per DM segment.
    """
    permission_classes = [IsAuthenticated]

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
