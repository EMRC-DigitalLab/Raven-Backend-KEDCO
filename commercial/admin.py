from django.contrib import admin
from .models import (Customer,
                     MonthlyEnergyBilled, MonthlyRevenueBilled, MonthlyCustomerStats,
                     SalesRepresentative, SalesRepPerformance, DailyCollection,
                     MonthlyCommercialSummary)

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'metering_type', 'band', 'transformer']
    search_fields = ['name']
    list_filter = ['category', 'metering_type', 'band']


@admin.register(MonthlyEnergyBilled)
class MonthlyEnergyBilledAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'month', 'energy_mwh']
    list_filter = ['month',]


@admin.register(MonthlyRevenueBilled)
class MonthlyRevenueBilledAdmin(admin.ModelAdmin):
    list_display = ['month', 'amount']
    list_filter = ['month',]


@admin.register(MonthlyCustomerStats)
class MonthlyCustomerStatsAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'month', 'customers_billed', 'customer_response_count']
    list_filter = ['month',]

@admin.register(SalesRepresentative)
class SalesRepresentativeAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    search_fields = ['name']


@admin.register(SalesRepPerformance)
class SalesRepPerformanceAdmin(admin.ModelAdmin):
    list_display = ['sales_rep', 'month', 'current_billed', 'collections']
    list_filter = ['sales_rep', 'month']


@admin.register(DailyCollection)
class DailyCollectionAdmin(admin.ModelAdmin):
    list_display = ['sales_rep', 'date', 'amount', 'collection_type', 'vendor_name']
    list_filter = ['collection_type', 'date']

@admin.register(MonthlyCommercialSummary)
class MonthlyCommercialSummaryAdmin(admin.ModelAdmin):
    list_display = ['sales_rep', 'month', 'customers_billed', 'customers_responded', 'revenue_billed', 'revenue_collected',]