# reports/urls.py
from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # Template CRUD
    path('templates/', views.ReportTemplateListCreateView.as_view(), name='template-list-create'),
    path('templates/<uuid:pk>/', views.ReportTemplateDetailView.as_view(), name='template-detail'),
    path('templates/<uuid:template_id>/clone/', views.clone_template, name='template-clone'),
    
    # Section management
    path('templates/<uuid:template_id>/sections/', views.add_section_to_template, name='add-section'),
    path('templates/<uuid:template_id>/sections/<uuid:section_id>/', views.update_section, name='update-section'),
    path('templates/<uuid:template_id>/sections/reorder/', views.reorder_sections, name='reorder-sections'),
    
    # Available options
    path('sections/available/', views.available_sections, name='available-sections'),
    path('filters/options/', views.filter_options, name='filter-options'),
    
    # Data preview
    path('preview/section/', views.preview_section_data, name='preview-section'),
    path('preview/all/', views.preview_all_sections, name='preview-all'),
    
    # PDF generation
    path('generate/pdf/', views.generate_report_pdf, name='generate-pdf'),
    path('generate/html-preview/', views.generate_report_html_preview, name='generate-html-preview'),
    
    # History
    path('history/', views.GeneratedReportListView.as_view(), name='report-history'),
]