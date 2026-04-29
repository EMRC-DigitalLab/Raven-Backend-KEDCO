"""
raven/views.py

GET /api/version/

Returns server startup time, git commit, branch, and environment.
No authentication required — safe to expose publicly.
"""

import subprocess
from datetime import datetime, timezone

from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

# Captured once at process startup — closest thing to "deploy time"
_SERVER_STARTED_AT = datetime.now(tz=timezone.utc)


def _git(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(
            ['git'] + cmd,
            stderr=subprocess.DEVNULL,
            cwd=settings.BASE_DIR,
        ).decode().strip()
    except Exception:
        return 'unknown'


@api_view(['GET'])
@permission_classes([AllowAny])
def version(request):
    """
    GET /api/version/

    Returns:
      environment    : "production" | "staging" | "development"
      server_started : ISO 8601 timestamp when this process started (= deploy time on Render)
      git_commit     : short SHA of the deployed commit
      git_branch     : branch name
      git_message    : subject line of the last commit
    """
    return Response({
        'environment':    getattr(settings, 'APP_ENV', 'unknown'),
        'server_started': _SERVER_STARTED_AT.isoformat(),
        'git_commit':     _git(['rev-parse', '--short', 'HEAD']),
        'git_branch':     _git(['rev-parse', '--abbrev-ref', 'HEAD']),
        'git_message':    _git(['log', '-1', '--format=%s']),
    })
