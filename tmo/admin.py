from django.contrib import admin
from .models import TMOMonthlySegmentTarget


@admin.register(TMOMonthlySegmentTarget)
class TMOMonthlySegmentTargetAdmin(admin.ModelAdmin):
    list_display  = ('segment', 'year', 'month', 'target_energy_mwh', 'target_revenue_ngn', 'target_collection_ngn')
    list_filter   = ('segment', 'year')
    ordering      = ('-year', '-month', 'segment')
