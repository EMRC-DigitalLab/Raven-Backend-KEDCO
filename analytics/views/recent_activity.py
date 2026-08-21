# analytics/views/recent_activity.py
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class RecentActivityAPIView(APIView):
    """
    GET /api/activity/recent/?limit=4

    Cross-module activity feed. Open to any authenticated user.

    Honest caveat: ActivityLog (analytics/models.py) is new — nothing in the
    codebase writes to it yet, so this will return an empty list until
    specific call sites (report generation, user creation, feeder
    reclassification, etc.) start calling ActivityLog.record(...) at the
    point those actions happen. Not backfilled from history.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from analytics.models import ActivityLog

        try:
            limit = min(int(request.GET.get('limit', 4)), 50)
        except (TypeError, ValueError):
            limit = 4

        rows = ActivityLog.objects.select_related('actor')[:limit]
        return Response([
            {
                'label': r.label,
                'time': r.created_at.isoformat(),
                'type': r.activity_type,
                'actor': (r.actor.get_full_name() or r.actor.username) if r.actor else None,
            }
            for r in rows
        ])
