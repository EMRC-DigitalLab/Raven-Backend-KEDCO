# analytics/views/attention_required.py
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class AttentionRequiredAPIView(APIView):
    """
    GET /api/analytics/alerts/

    Cross-module flags: feeders below supply target (Technical/TMO),
    active TMO incidents, and commercial reading coverage. Open to any
    authenticated user.

    Note: "Incidents" here is TMOIncident specifically — there is no
    generic cross-module incidents table in the system, only TMO's.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from commercial.analytics_utils import calc_coverage
        from commercial.models import CommercialCustomer, MeterReading
        from tmo.models import TMOIncident
        from tmo.services import TMOService

        today = date.today()
        month_start = today.replace(day=1)

        alerts = []

        # ── Feeders below supply target (TMO) ───────────────────────────────
        compliance = TMOService(month_start, today).get_supply_compliance()
        below_target = [
            r for r in compliance['feeders']
            if r['status'] in ('below_target', 'poor')
        ]
        if below_target:
            severity = 'critical' if len(below_target) > 10 else 'warning'
            alerts.append({
                'type': severity,
                'label': f"{len(below_target)} feeder{'s' if len(below_target) != 1 else ''} below supply target this month",
                'route': '/tmo/tmo-dashboard',
            })

        # ── Active TMO incidents ─────────────────────────────────────────────
        active_incidents = TMOIncident.objects.filter(status='lingering').count()
        if active_incidents:
            alerts.append({
                'type': 'critical' if active_incidents > 5 else 'warning',
                'label': f"{active_incidents} active incident{'s' if active_incidents != 1 else ''} unresolved",
                'route': '/tmo/tmo-dashboard',
            })

        # ── Commercial reading coverage ──────────────────────────────────────
        customers_qs = CommercialCustomer.objects.filter(is_active=True) \
            if hasattr(CommercialCustomer, 'is_active') else CommercialCustomer.objects.all()
        readings_qs = MeterReading.objects.filter(reading_date__gte=month_start, reading_date__lte=today)
        coverage = calc_coverage(customers_qs, readings_qs)
        rate = coverage['rate']
        if rate < 90:
            alerts.append({
                'type': 'warning' if rate >= 75 else 'critical',
                'label': f"Meter reading coverage at {rate}% this month",
                'route': '/commercial/commercial-dashboard',
            })
        else:
            alerts.append({
                'type': 'info',
                'label': f"Meter reading coverage at {rate}% this month",
                'route': '/commercial/commercial-dashboard',
            })

        return Response(alerts)
