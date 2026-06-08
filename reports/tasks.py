"""
reports/tasks.py

Celery tasks for background report generation.
"""

import base64
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name='reports.tasks.generate_management_report_task',
    bind=True,
    max_retries=0,
    time_limit=600,       # 10-minute hard ceiling
    soft_time_limit=540,  # 9-minute soft warning
)
def generate_management_report_task(self, report_config: dict, filters: dict, user_id: int):
    """
    Generate a management PDF report in the background.

    Queued by POST /api/reports/generate/management/.
    Frontend polls GET /api/reports/generate/management/<job_id>/ until
    state == SUCCESS, then uses pdf_base64 to trigger a client-side download.

    Result on SUCCESS:
        {
            "status": "done",
            "pdf_base64": "<base64 string>",
            "filename": "May_2026_Management_Report.pdf"
        }
    """
    self.update_state(state='PROGRESS', meta={'stage': 'Fetching report data'})

    try:
        from django.contrib.auth import get_user_model
        from reports.services import ReportDataService
        from reports.management_report import ManagementPDFGenerator

        User = get_user_model()
        user = User.objects.get(id=user_id)

        data_service = ReportDataService(filters, user=user)

        self.update_state(state='PROGRESS', meta={'stage': 'Rendering PDF'})

        generator = ManagementPDFGenerator(report_config, data_service)
        pdf_buffer = generator.generate_pdf()

        pdf_b64 = base64.b64encode(pdf_buffer.read()).decode('utf-8')
        filename = report_config.get('report_title', 'Management_Report').replace(' ', '_') + '.pdf'

        return {
            'status': 'done',
            'pdf_base64': pdf_b64,
            'filename': filename,
        }

    except Exception as exc:
        logger.exception('Management report task failed: %s', exc)
        raise