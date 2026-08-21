# analytics/views/revenue_daily.py
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class RevenueDailyAPIView(APIView):
    """
    GET /api/analytics/revenue/daily/?month=8&year=2026

    Daily revenue series for the Revenue Progress chart: each day's actual
    (tariff-valued) revenue, a running cumulative total, and a straight-line
    target pace for comparison. Open to any authenticated user.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from tmo.models import TMOMonthlySegmentTarget
        from tmo.services import TMOService

        try:
            month = int(request.GET.get('month'))
            year = int(request.GET.get('year'))
        except (TypeError, ValueError):
            today = date.today()
            month, year = today.month, today.year

        month_start = date(year, month, 1)
        month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)
        today = date.today()
        window_end = min(month_end, today)
        days_in_month = (month_end - month_start).days + 1

        # ₦/kWh per segment for this month (falls back to the most recent
        # month with targets — same rule get_gcr() itself uses).
        targets = {
            t.segment: t
            for t in TMOMonthlySegmentTarget.objects.filter(year=year, month=month)
        }
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
                    for t in TMOMonthlySegmentTarget.objects.filter(year=latest[0], month=latest[1])
                }
        tariff_map = {seg: float(t.average_tariff_per_kwh) for seg, t in targets.items()}
        target_mwh_total = sum(float(t.target_energy_mwh) for t in targets.values())
        expected_bill_total = sum(
            float(t.target_energy_mwh) * 1_000 * float(t.average_tariff_per_kwh)
            for t in targets.values()
        )
        expected_daily_bn = (expected_bill_total / days_in_month) / 1e9 if days_in_month else 0.0

        svc = TMOService(month_start, window_end)
        by_day = svc._segment_voltage_daily_map(month_start, window_end)

        series = []
        running_total_bn = 0.0
        d = month_start
        day_num = 0
        while d <= window_end:
            day_num += 1
            d_str = str(d)
            day_segments = by_day.get(d_str, {})
            day_revenue = 0.0
            for seg_name, tariff in tariff_map.items():
                v = day_segments.get(seg_name, {'33kv': 0.0, '11kv': 0.0})
                mwh = v['33kv'] + v['11kv']
                day_revenue += mwh * 1_000 * tariff
            running_total_bn += day_revenue / 1e9
            series.append({
                'date': d_str,
                'projectedRevenue': round(running_total_bn, 4),
                'expectedRevenue': round(expected_daily_bn * day_num, 4),
            })
            d += timedelta(days=1)

        return Response({
            'period': {'from': str(month_start), 'to': str(month_end)},
            'series': series,
        })
