# technical/views/fault_analytics.py
"""
CTO dashboard endpoints: TMO's existing KPIs cover the energy/dispatch side
(see tmo/views.py) -- these cover the fault-analytics side, built on top of
FeederInterruption(source='tcn') via technical/services/fault_analytics.py.

Period selection deliberately reuses tmo.services.resolve_date_params() --
the exact same ?from_date=&to_date= / ?month=YYYY-MM / ?date= global filter
already used by every TMO endpoint -- rather than inventing a new scheme,
so the frontend's existing global date picker works here unchanged.

All endpoints require HasCTOAccess: a dedicated 'cto' section assignment,
separate from TMO/Technical module access -- seeing this dashboard is its
own explicit grant, not implied by any other module permission.
"""
from datetime import date

from rest_framework.response import Response
from rest_framework.views import APIView

from tmo.services import resolve_date_params

from technical.permissions import HasCTOAccess
from technical.services import fault_analytics as svc


class CTOTCNInterruptionsView(APIView):
    """GET /api/technical/cto/tcn-interruptions/ -- per-feeder count split by party_responsible."""
    permission_classes = [HasCTOAccess]

    def get(self, request):
        from_date, to_date = resolve_date_params(request)
        return Response(svc.compute_tcn_interruptions_by_feeder(from_date, to_date))


class CTOFeederComplianceView(APIView):
    """GET /api/technical/cto/feeder-compliance/ -- total interruption count per 33kV feeder."""
    permission_classes = [HasCTOAccess]

    def get(self, request):
        from_date, to_date = resolve_date_params(request)
        return Response(svc.compute_feeder_compliance(from_date, to_date))


class CTOPeakLoadView(APIView):
    """GET /api/technical/cto/peak-load/ -- highest peak load per 33kV feeder for any selected period."""
    permission_classes = [HasCTOAccess]

    def get(self, request):
        from_date, to_date = resolve_date_params(request)
        return Response(svc.compute_peak_load_ranking(from_date, to_date))


class CTOFRIRankingsView(APIView):
    """GET /api/technical/cto/fri-rankings/ -- full Feeder Risk Index ranking for the selected period."""
    permission_classes = [HasCTOAccess]

    def get(self, request):
        from_date, to_date = resolve_date_params(request)
        return Response(svc.compute_fri_rankings(from_date, to_date))


class CTORiskDistributionView(APIView):
    """GET /api/technical/cto/risk-distribution/ -- feeder counts/outages/ENS grouped by risk category."""
    permission_classes = [HasCTOAccess]

    def get(self, request):
        from_date, to_date = resolve_date_params(request)
        return Response(svc.compute_risk_distribution(from_date, to_date))


class CTOPenaltyDriversView(APIView):
    """GET /api/technical/cto/penalty-drivers/ -- top 20% (Pareto) feeders by FRI for the selected period."""
    permission_classes = [HasCTOAccess]

    def get(self, request):
        from_date, to_date = resolve_date_params(request)
        return Response(svc.compute_penalty_drivers(from_date, to_date))


class CTOChronicFaultFeedersView(APIView):
    """
    GET /api/technical/cto/chronic-fault-feeders/?year=2026 -- feeders that
    fault repeatedly month after month, ranked by how many months they
    landed in CRITICAL/HIGH risk (not a single-period snapshot). Year-scoped
    like monthly-summary, same reasoning: this is inherently "across the
    year," not a single from_date/to_date period.
    """
    permission_classes = [HasCTOAccess]

    def get(self, request):
        year = int(request.query_params.get('year', date.today().year))
        return Response(svc.compute_chronic_fault_feeders(year))


class CTOMonthlySummaryView(APIView):
    """
    GET /api/technical/cto/monthly-summary/?year=2026 -- YTD Monthly Cumulative
    Summary, one row per month plus a YTD_TOTAL row. Year-scoped rather than
    the from_date/to_date global filter, since this widget is inherently
    "every month of a year," not a single period. Defaults to the current year.
    """
    permission_classes = [HasCTOAccess]

    def get(self, request):
        year = int(request.query_params.get('year', date.today().year))
        return Response(svc.compute_monthly_summary(year))
