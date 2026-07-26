# tmo/views.py
from datetime import date, timedelta

from django.core.exceptions import ObjectDoesNotExist

from datetime import date, timedelta

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
