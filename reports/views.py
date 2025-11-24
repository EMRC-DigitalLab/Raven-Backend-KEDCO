# reports/views.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import (
    ListCreateAPIView, 
    RetrieveUpdateDestroyAPIView,
    ListAPIView,
)
from django.db import models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
import logging

from .models import ReportTemplate, ReportSection, GeneratedReport
from .serializers import (
    ReportTemplateListSerializer,
    ReportTemplateDetailSerializer,
    ReportTemplateCreateSerializer,
    ReportSectionSerializer,
    GeneratedReportSerializer,
    ReportGenerateRequestSerializer,
    AvailableSectionSerializer,
)
from .services import ReportDataService, get_available_sections, SECTION_DEFINITIONS
from .pdf_generator import PDFGenerator

from common.models import State, BusinessDistrict, InjectionSubstation, Feeder, Band

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
    permission_classes = [IsAuthenticated]
    
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
    permission_classes = [IsAuthenticated]
    
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
@permission_classes([IsAuthenticated])
def available_sections(request):
    """
    Get list of available section types with their configurations.
    """
    sections = get_available_sections()
    return Response({
        "sections": sections,
        "categories": [
            {"id": "general", "name": "General"},
            {"id": "technical", "name": "Technical"},
            {"id": "commercial", "name": "Commercial"},
            {"id": "financial", "name": "Financial"},
        ]
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
def filter_options(request):
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
        'business_district__name', 'substation_id', 'business_district_id', 'band_id'
    ).order_by('name')
    
    return Response({
        "states": list(states),
        "districts": list(districts),
        "substations": list(substations),
        "bands": list(bands),
        "feeders": list(feeders),
    })


# =============================================================================
# REPORT DATA PREVIEW
# =============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
# GENERATED REPORTS HISTORY
# =============================================================================

class GeneratedReportListView(ListAPIView):
    """
    List generated reports history for the current user.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = GeneratedReportSerializer
    
    def get_queryset(self):
        return GeneratedReport.objects.filter(generated_by=self.request.user)


# =============================================================================
# CLONE TEMPLATE
# =============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
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