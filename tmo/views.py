# tmo/views.py
from django.core.exceptions import ObjectDoesNotExist

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import TMOService, resolve_date_params


def _filters_from_request(request):
    p = request.query_params
    return {k: p.get(k) for k in ('segment', 'state', 'district', 'band', 'voltage', 'feeder') if p.get(k)}


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
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            data = _make_service(request).get_supply_compliance()
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
