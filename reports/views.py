# reports/views.py
import logging

from django.db import models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from common.models import Band, BusinessDistrict, Feeder, InjectionSubstation, State

from .models import GeneratedReport, ReportSection, ReportTemplate
from .pdf_generator import PDFGenerator
from .serializers import (
    GeneratedReportSerializer,
    ReportGenerateRequestSerializer,
    ReportSectionSerializer,
    ReportTemplateCreateSerializer,
    ReportTemplateDetailSerializer,
    ReportTemplateListSerializer,
)
from .services import SECTION_DEFINITIONS, ReportDataService, get_available_sections

logger = logging.getLogger(__name__)


# =============================================================================
# TEMPLATE CRUD VIEWS
# =============================================================================

class ReportTemplateListCreateView(ListCreateAPIView):
    """
    List all report templates or create a new one.
    
    GET: List templates (filtered by user unless is_public=True)
    POST: Create a new template
    """
    permission_classes = [AllowAny]
    
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ReportTemplateCreateSerializer
        return ReportTemplateListSerializer
    
    def get_queryset(self):
        user = self.request.user
        # Return user's own templates and public templates
        return ReportTemplate.objects.filter(
            models.Q(created_by=user) | models.Q(is_public=True)
        ).distinct()


class ReportTemplateDetailView(RetrieveUpdateDestroyAPIView):
    """
    Retrieve, update, or delete a report template.
    """
    permission_classes = [AllowAny]
    
    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return ReportTemplateCreateSerializer
        return ReportTemplateDetailSerializer
    
    def get_queryset(self):
        user = self.request.user
        return ReportTemplate.objects.filter(
            models.Q(created_by=user) | models.Q(is_public=True)
        )
    
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        # Only allow deletion of own templates
        if instance.created_by != request.user:
            return Response(
                {"error": "You can only delete your own templates"},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)


# =============================================================================
# SECTION MANAGEMENT
# =============================================================================

@api_view(['GET'])
def available_sections(request):
    """
    Get list of available section types filtered by the requesting user's module access.
    """
    sections = get_available_sections(request.user)
    return Response({
        "sections": sections,
        "categories": [
            {"id": "general",     "name": "General"},
            {"id": "technical",   "name": "Technical"},
            {"id": "hr",          "name": "Human Resources"},
            {"id": "commercial",  "name": "Commercial"},
            {"id": "financial",   "name": "Financial"},
            {"id": "comparison",  "name": "Comparisons"},
        ]
    })


@api_view(['POST'])
def add_section_to_template(request, template_id):
    """
    Add a new section to an existing template.
    """
    template = get_object_or_404(ReportTemplate, id=template_id, created_by=request.user)
    
    serializer = ReportSectionSerializer(data=request.data)
    if serializer.is_valid():
        # Get the next order number
        max_order = template.sections.aggregate(models.Max('order'))['order__max'] or 0
        serializer.save(template=template, order=max_order + 1)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT', 'DELETE'])
def update_section(request, template_id, section_id):
    """
    Update or delete a section.
    """
    template = get_object_or_404(ReportTemplate, id=template_id, created_by=request.user)
    section = get_object_or_404(ReportSection, id=section_id, template=template)
    
    if request.method == 'DELETE':
        section.delete()
        # Reorder remaining sections
        for i, sec in enumerate(template.sections.all()):
            sec.order = i
            sec.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    # PUT - Update
    serializer = ReportSectionSerializer(section, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def reorder_sections(request, template_id):
    """
    Reorder sections within a template.
    
    Request body:
    {
        "section_order": [uuid1, uuid2, uuid3, ...]
    }
    """
    template = get_object_or_404(ReportTemplate, id=template_id, created_by=request.user)
    section_order = request.data.get('section_order', [])
    
    for i, section_id in enumerate(section_order):
        try:
            section = ReportSection.objects.get(id=section_id, template=template)
            section.order = i
            section.save()
        except ReportSection.DoesNotExist:
            continue
    
    return Response({"message": "Sections reordered successfully"})


# =============================================================================
# FILTER OPTIONS
# =============================================================================

@api_view(['GET'])
def filter_options(_request):
    """
    Get available filter options (states, districts, substations, bands, feeders).
    """
    states = State.objects.all().values('id', 'name').order_by('name')
    districts = BusinessDistrict.objects.all().select_related('state').values(
        'id', 'name', 'state__name', 'state_id'
    ).order_by('name')
    substations = InjectionSubstation.objects.all().values('id', 'name').order_by('name')
    bands = Band.objects.all().values('id', 'name', 'description').order_by('name')
    feeders = Feeder.objects.all().select_related(
        'band', 'substation', 'business_district'
    ).values(
        'id', 'name', 'band__name', 'substation__name',
        'business_district__name', 'substation_id', 'business_district_id', 'band_id',
        'voltage_level',
    ).order_by('name')

    voltage_levels = [
        {'id': '11kv', 'name': '11kV Feeders'},
        {'id': '33kv', 'name': '33kV Feeders'},
    ]

    return Response({
        "states": list(states),
        "districts": list(districts),
        "substations": list(substations),
        "bands": list(bands),
        "feeders": list(feeders),
        "voltage_levels": voltage_levels,
    })


# =============================================================================
# REPORT DATA PREVIEW
# =============================================================================

@api_view(['POST'])
def preview_section_data(request):
    """
    Get data for a specific section based on filters.
    Used for real-time preview in the report builder.
    
    Request body:
    {
        "section_type": "technical_metrics",
        "config": {...},
        "filters": {
            "from_date": "2025-01-01",
            "to_date": "2025-01-31",
            "states": [],
            "districts": [],
            ...
        }
    }
    """
    section_type = request.data.get('section_type')
    config = request.data.get('config', {})
    filters = request.data.get('filters', {})
    
    if not section_type:
        return Response(
            {"error": "section_type is required"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    if not filters.get('from_date') or not filters.get('to_date'):
        return Response(
            {"error": "from_date and to_date filters are required"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        data_service = ReportDataService(filters)
        data = data_service.get_all_section_data(section_type, config)
        
        return Response({
            "section_type": section_type,
            "data": data,
        })
    except Exception as e:
        logger.error(f"Error fetching section data: {str(e)}")
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
def preview_all_sections(request):
    """
    Get data for all sections in a report configuration.
    Used for full report preview.
    
    Request body:
    {
        "sections": [
            {"section_type": "cover_page", "config": {}},
            {"section_type": "technical_metrics", "config": {...}},
        ],
        "filters": {...}
    }
    """
    sections = request.data.get('sections', [])
    filters = request.data.get('filters', {})
    
    if not filters.get('from_date') or not filters.get('to_date'):
        return Response(
            {"error": "from_date and to_date filters are required"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        data_service = ReportDataService(filters)
        
        sections_data = []
        for section in sections:
            section_type = section.get('section_type')
            config = section.get('config', {})
            
            data = data_service.get_all_section_data(section_type, config)
            sections_data.append({
                "section_type": section_type,
                "config": config,
                "data": data,
            })
        
        return Response({
            "sections": sections_data,
            "period": {
                "from_date": str(data_service.from_date),
                "to_date": str(data_service.to_date),
                "days": data_service.period_days,
            }
        })
    except Exception as e:
        logger.error(f"Error fetching report data: {str(e)}")
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# =============================================================================
# PDF GENERATION
# =============================================================================

@api_view(['POST'])
def generate_report_pdf(request):
    """
    Generate a PDF report.
    
    Request body:
    {
        "report_title": "October 2025 Performance Report",
        "report_subtitle": "",
        "orientation": "portrait",
        "company_name": "KANO ELECTRICITY DISTRIBUTION COMPANY",
        "sections": [
            {"section_type": "cover_page", "config": {}},
            {"section_type": "technical_metrics", "config": {...}},
        ],
        "filters": {
            "from_date": "2025-10-01",
            "to_date": "2025-10-31",
            ...
        },
        "save_history": true  // Optional - save to GeneratedReport
    }
    """
    serializer = ReportGenerateRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    filters = data.get('filters', {})
    
    if not filters.get('from_date') or not filters.get('to_date'):
        return Response(
            {"error": "from_date and to_date filters are required"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Create data service
        data_service = ReportDataService(filters)
        
        # Build report config
        report_config = {
            'report_title': data.get('report_title', 'Performance Report'),
            'report_subtitle': data.get('report_subtitle', ''),
            'orientation': data.get('orientation', 'portrait'),
            'company_name': request.data.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
            'sections': data.get('sections', []),
            'theme': data.get('theme', {}),
        }
        
        # Generate PDF
        pdf_generator = PDFGenerator(report_config, data_service)
        pdf_buffer = pdf_generator.generate_pdf()
        
        # Optionally save to history (fail silently if table doesn't exist)
        if request.data.get('save_history', False):
            try:
                GeneratedReport.objects.create(
                    template_id=data.get('template_id'),
                    report_title=report_config['report_title'],
                    filters_used=filters,
                    sections_included=[s['section_type'] for s in report_config['sections']],
                    generated_by=request.user,
                )
            except Exception as save_error:
                logger.warning(f"Could not save report history: {save_error}")
                # Continue anyway - PDF generation succeeded
        
        # Return PDF file
        response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
        filename = f"{report_config['report_title'].replace(' ', '_')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        logger.error(f"Error generating PDF: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
def generate_report_data(request):
    """
    POST /api/reports/generate/data/

    Unified JSON data endpoint — the frontend receives structured section data
    and is responsible for rendering and generating the PDF client-side.

    Same request body as /generate/pdf/ with one addition:
        "company_name": "KANO ELECTRICITY DISTRIBUTION COMPANY"  // optional

    Response:
    {
        "report_id": "<uuid>",          // audit trail ID
        "report_title": "...",
        "report_subtitle": "...",
        "orientation": "portrait",
        "company_name": "...",
        "generated_at": "<iso8601>",
        "generated_by": "Full Name",
        "period": { "from_date", "to_date", "days", "label" },
        "sections_denied": ["hr_overview"],  // stripped due to role
        "sections": [
            {
                "section_type": "technical_metrics",
                "title": "",
                "config": {},
                "data": { ... }           // pure data, no rendering hints
            }
        ]
    }

    Comparison sections (entity_comparison, period_comparison, customer_comparison)
    are first-class section types here — the compare engine is called internally
    and the result is embedded in the section's data field.

    Always writes an audit entry to GeneratedReport (generation_method='data').
    The old /generate/pdf/ endpoint is untouched for backward compatibility.
    """
    serializer = ReportGenerateRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    filters = data.get('filters', {})
    user = request.user

    if not filters.get('from_date') or not filters.get('to_date'):
        return Response(
            {'error': 'from_date and to_date are required in filters'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Role-based section filtering ──────────────────────────────────────────
    allowed_section_types = {s['section_type'] for s in get_available_sections(user)}
    requested_sections = data.get('sections', [])

    allowed_sections = []
    denied_sections = []
    for section in requested_sections:
        st = section.get('section_type', '')
        if st in allowed_section_types:
            allowed_sections.append(section)
        else:
            denied_sections.append(st)

    try:
        data_service = ReportDataService(filters, user=user)

        sections_data = []
        for section in allowed_sections:
            section_type = section.get('section_type', '')
            config = section.get('config', {})
            title = section.get('title', '')

            if section_type == 'table_of_contents':
                # Build TOC from the other allowed sections so the frontend
                # can render a live, accurate table of contents.
                section_payload = {
                    'entries': [
                        {
                            'section_type': s.get('section_type'),
                            'title': (
                                s.get('title')
                                or SECTION_DEFINITIONS.get(s.get('section_type', ''), {}).get('display_name', '')
                            ),
                            'order': i,
                        }
                        for i, s in enumerate(allowed_sections)
                        if s.get('section_type') not in ('cover_page', 'table_of_contents')
                    ]
                }
            else:
                section_payload = data_service.get_all_section_data(section_type, config)

            sections_data.append({
                'section_type': section_type,
                'title': title,
                'config': config,
                'data': section_payload,
            })

        company_name  = request.data.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
        period_label  = ReportDataService._period_label(data_service.from_date, data_service.to_date)

        # ── AI insights (opt-in) ──────────────────────────────────────────────
        ai_insights_result = None
        if request.data.get('include_ai_insights', False):
            try:
                from analytics.services.report_insights import get_report_insights
                ai_insights_result = get_report_insights(
                    sections_data=sections_data,
                    period_label=period_label,
                    company_name=company_name,
                    include_summary=request.data.get('include_ai_summary', True),
                )
                # Attach per-section insights into each section's dict
                if ai_insights_result:
                    per_section = ai_insights_result.get('sections', {})
                    for s in sections_data:
                        s['ai_insights'] = per_section.get(s['section_type'])
            except Exception as ai_err:
                logger.warning('AI insights generation failed: %s', ai_err)
                ai_insights_result = {'error': str(ai_err), 'sections': {}, 'overall': None}

        # ── Audit trail — always recorded ────────────────────────────────────
        report_id = None
        try:
            generated_report = GeneratedReport.objects.create(
                template_id=data.get('template_id'),
                report_title=data.get('report_title', 'Performance Report'),
                filters_used=filters,
                sections_included=[s['section_type'] for s in allowed_sections],
                generated_by=user,
                generation_method='data',
            )
            report_id = str(generated_report.id)

            # Fire report_generated signal so the requesting user gets an in-app notification
            try:
                from notifications.signals import report_generated as _rg_signal
                _rg_signal.send(
                    sender=generated_report,
                    user=user,
                    report_type=generated_report.category or 'performance',
                    report_title=generated_report.report_title,
                    report_object_id=report_id,
                )
            except Exception as sig_err:
                logger.warning('Could not fire report_generated signal: %s', sig_err)

        except Exception as save_error:
            logger.warning('Could not save report audit entry: %s', save_error)

        return Response({
            'report_id': report_id,
            'report_title': data.get('report_title', 'Performance Report'),
            'report_subtitle': data.get('report_subtitle', ''),
            'orientation': data.get('orientation', 'portrait'),
            'theme': data.get('theme', {}),
            'company_name': company_name,
            'generated_at': timezone.now().isoformat(),
            'generated_by': user.get_full_name() or user.username,
            'period': {
                'from_date': str(data_service.from_date),
                'to_date': str(data_service.to_date),
                'days': data_service.period_days,
                'label': period_label,
            },
            'sections_denied': denied_sections,
            'sections': sections_data,
            'ai_summary': ai_insights_result.get('overall') if ai_insights_result else None,
        })

    except Exception as e:
        logger.error('Error generating report data: %s', e)
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def generate_report_html_preview(request):
    """
    Generate HTML preview of the report (for frontend rendering).
    Returns the raw HTML that can be displayed in an iframe.
    """
    filters = request.data.get('filters', {})
    
    if not filters.get('from_date') or not filters.get('to_date'):
        return Response(
            {"error": "from_date and to_date filters are required"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        data_service = ReportDataService(filters)
        
        report_config = {
            'report_title': request.data.get('report_title', 'Performance Report'),
            'report_subtitle': request.data.get('report_subtitle', ''),
            'orientation': request.data.get('orientation', 'portrait'),
            'company_name': request.data.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
            'sections': request.data.get('sections', []),
            'theme': request.data.get('theme', {}),
        }
        
        pdf_generator = PDFGenerator(report_config, data_service)
        html_content = pdf_generator.generate_html()
        
        return Response({
            "html": html_content
        })
        
    except Exception as e:
        logger.error(f"Error generating HTML preview: {str(e)}")
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# =============================================================================
# MANAGEMENT / ADMIN REPORT
# =============================================================================

@api_view(['POST'])
def generate_management_report(request):
    """
    POST /api/reports/generate/management/

    Queues a management PDF report for background generation and immediately
    returns a job_id.  Poll GET /api/reports/generate/management/<job_id>/
    every 3-5 seconds until state == SUCCESS, then use pdf_base64 to trigger
    a client-side download.

    Body:
    {
        "report_title":    "May 2026 11kV Management Report",       // optional
        "report_subtitle": "",                                       // optional
        "company_name":    "KANO ELECTRICITY DISTRIBUTION COMPANY", // optional
        "theme":           { "primary_color": "#002050", ... },     // optional
        "include_ai":      true,                                     // default true
        "filters": {
            "from_date": "2026-05-01",
            "to_date":   "2026-05-31",
            ...
        }
    }

    Response 202:
    {
        "job_id":     "<celery-uuid>",
        "status_url": "/api/reports/generate/management/<job_id>/",
        "message":    "Report queued. Poll status_url for progress."
    }
    """
    from .tasks import generate_management_report_task

    filters = request.data.get('filters', {})
    if not filters.get('from_date') or not filters.get('to_date'):
        return Response(
            {'error': 'from_date and to_date are required in filters'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    report_config = {
        'report_title':    request.data.get('report_title', 'Management Report'),
        'report_subtitle': request.data.get('report_subtitle', ''),
        'company_name':    request.data.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
        'theme':           request.data.get('theme', {}),
        'include_ai':      bool(request.data.get('include_ai', True)),
    }

    task = generate_management_report_task.apply_async(
        kwargs={
            'report_config': report_config,
            'filters':       filters,
            'user_id':       request.user.id,
        },
        queue='default',
    )

    status_url = f'/api/reports/generate/management/{task.id}/'
    return Response(
        {
            'job_id':     task.id,
            'status_url': status_url,
            'message':    'Report queued. Poll status_url for progress.',
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['GET'])
def management_report_status(request, job_id):
    """
    GET /api/reports/generate/management/<job_id>/

    Poll until state == SUCCESS, then decode pdf_base64 and trigger a download.

    Response while running:
    {
        "job_id": "...",
        "state":  "PROGRESS",
        "stage":  "Rendering PDF"
    }

    Response on success:
    {
        "job_id":     "...",
        "state":      "SUCCESS",
        "pdf_base64": "<base64>",
        "filename":   "May_2026_Management_Report.pdf"
    }

    Response on failure:
    {
        "job_id": "...",
        "state":  "FAILURE",
        "error":  "..."
    }
    """
    from celery.result import AsyncResult

    result = AsyncResult(job_id)
    state  = result.state

    if state == 'PROGRESS':
        meta = result.info or {}
        return Response({
            'job_id': job_id,
            'state':  'PROGRESS',
            'stage':  meta.get('stage', 'Working…'),
        })

    if state == 'SUCCESS':
        res = result.result or {}
        return Response({
            'job_id':     job_id,
            'state':      'SUCCESS',
            'pdf_base64': res.get('pdf_base64'),
            'filename':   res.get('filename', 'Management_Report.pdf'),
        })

    if state == 'FAILURE':
        return Response({
            'job_id': job_id,
            'state':  'FAILURE',
            'error':  str(result.result),
        })

    # PENDING / STARTED / REVOKED
    return Response({'job_id': job_id, 'state': state})


# =============================================================================
# GENERATED REPORTS HISTORY
# =============================================================================

class GeneratedReportListView(ListAPIView):
    """
    List generated reports history for the current user.
    """
    permission_classes = [AllowAny]
    serializer_class = GeneratedReportSerializer
    
    def get_queryset(self):
        return GeneratedReport.objects.filter(generated_by=self.request.user)


# =============================================================================
# CLONE TEMPLATE
# =============================================================================

@api_view(['POST'])
def clone_template(request, template_id):
    """
    Clone an existing template to create a new one.
    """
    original = get_object_or_404(
        ReportTemplate.objects.filter(
            models.Q(created_by=request.user) | models.Q(is_public=True)
        ),
        id=template_id
    )
    
    # Create new template
    new_template = ReportTemplate.objects.create(
        name=f"{original.name} (Copy)",
        description=original.description,
        report_title=original.report_title,
        report_subtitle=original.report_subtitle,
        orientation=original.orientation,
        default_filters=original.default_filters,
        created_by=request.user,
        status='draft',
        is_public=False,
    )
    
    # Clone sections
    for section in original.sections.all():
        ReportSection.objects.create(
            template=new_template,
            section_type=section.section_type,
            title=section.title,
            order=section.order,
            is_enabled=section.is_enabled,
            config=section.config,
            show_chart=section.show_chart,
            chart_type=section.chart_type,
        )
    
    serializer = ReportTemplateDetailSerializer(new_template)
    return Response(serializer.data, status=status.HTTP_201_CREATED)