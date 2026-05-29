# reports/serializers.py
from rest_framework import serializers

from .models import GeneratedReport, ReportSection, ReportTemplate


class ReportSectionSerializer(serializers.ModelSerializer):
    section_type_display = serializers.CharField(
        source='get_section_type_display', 
        read_only=True
    )

    class Meta:
        model = ReportSection
        fields = [
            'id',
            'section_type',
            'section_type_display',
            'title',
            'order',
            'is_enabled',
            'config',
            'show_chart',
            'chart_type',
        ]


class ReportTemplateListSerializer(serializers.ModelSerializer):
    """Serializer for listing templates (minimal data)"""
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', 
        read_only=True
    )
    section_count = serializers.SerializerMethodField()

    class Meta:
        model = ReportTemplate
        fields = [
            'id',
            'name',
            'description',
            'report_title',
            'orientation',
            'status',
            'is_public',
            'created_by_name',
            'section_count',
            'created_at',
            'updated_at',
        ]

    def get_section_count(self, obj):
        return obj.sections.filter(is_enabled=True).count()


class ReportTemplateDetailSerializer(serializers.ModelSerializer):
    """Serializer for full template details including sections"""
    sections = ReportSectionSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', 
        read_only=True
    )

    class Meta:
        model = ReportTemplate
        fields = [
            'id',
            'name',
            'description',
            'report_title',
            'report_subtitle',
            'orientation',
            'default_filters',
            'status',
            'is_public',
            'created_by',
            'created_by_name',
            'sections',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']


class ReportTemplateCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating templates"""
    sections = ReportSectionSerializer(many=True, required=False)

    class Meta:
        model = ReportTemplate
        fields = [
            'id',
            'name',
            'description',
            'report_title',
            'report_subtitle',
            'orientation',
            'default_filters',
            'status',
            'is_public',
            'sections',
        ]

    def create(self, validated_data):
        sections_data = validated_data.pop('sections', [])
        validated_data['created_by'] = self.context['request'].user
        template = ReportTemplate.objects.create(**validated_data)
        
        for idx, section_data in enumerate(sections_data):
            section_data['order'] = idx
            ReportSection.objects.create(template=template, **section_data)
        
        return template

    def update(self, instance, validated_data):
        sections_data = validated_data.pop('sections', None)
        
        # Update template fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update sections if provided
        if sections_data is not None:
            # Delete existing sections and recreate
            instance.sections.all().delete()
            for idx, section_data in enumerate(sections_data):
                section_data['order'] = idx
                ReportSection.objects.create(template=instance, **section_data)
        
        return instance


class GeneratedReportSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(
        source='template.name', 
        read_only=True,
        allow_null=True
    )
    generated_by_name = serializers.CharField(
        source='generated_by.get_full_name', 
        read_only=True
    )

    class Meta:
        model = GeneratedReport
        fields = [
            'id',
            'template',
            'template_name',
            'report_title',
            'category',
            'generation_method',
            'filters_used',
            'sections_included',
            'generated_by',
            'generated_by_name',
            'generated_at',
            'file_path',
            'file_size',
        ]
        read_only_fields = ['generated_by', 'generated_at']


class ReportGenerateRequestSerializer(serializers.Serializer):
    """Serializer for report generation requests"""
    # Template (optional - can generate without saving template)
    template_id = serializers.UUIDField(required=False, allow_null=True)
    
    # Report metadata
    report_title = serializers.CharField(max_length=255)
    report_subtitle = serializers.CharField(max_length=255, required=False, allow_blank=True)
    orientation = serializers.ChoiceField(choices=['portrait', 'landscape'], default='portrait')
    
    # Filters
    filters = serializers.DictField(required=False, default=dict)
    
    # Sections to include
    sections = serializers.ListField(
        child=serializers.DictField(),
        required=True
    )
    
    def validate_sections(self, value):
        if not value:
            raise serializers.ValidationError("At least one section is required")

        from .services import SECTION_DEFINITIONS
        valid_section_types = set(SECTION_DEFINITIONS.keys())

        for section in value:
            if 'section_type' not in section:
                raise serializers.ValidationError("Each section must have a section_type")
            if section['section_type'] not in valid_section_types:
                raise serializers.ValidationError(
                    f"Invalid section_type: {section['section_type']}"
                )

        return value


class AvailableSectionSerializer(serializers.Serializer):
    """Serializer for listing available section types"""
    section_type = serializers.CharField()
    display_name = serializers.CharField()
    description = serializers.CharField()
    category = serializers.CharField()
    supports_chart = serializers.BooleanField()
    config_options = serializers.DictField()