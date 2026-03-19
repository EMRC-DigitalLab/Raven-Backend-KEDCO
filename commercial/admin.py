from django.contrib import admin

from .models import CommercialCustomer, MeterReading, TariffRate


@admin.register(TariffRate)
class TariffRateAdmin(admin.ModelAdmin):
    list_display = ['band', 'customer_type', 'rate_per_kwh', 'effective_from', 'effective_to', 'is_active']
    list_filter = ['band', 'customer_type', 'is_active']


@admin.register(CommercialCustomer)
class CommercialCustomerAdmin(admin.ModelAdmin):
    list_display = ['customer_name', 'account_no', 'customer_type', 'feeder', 'district']
    search_fields = ['customer_name', 'account_no', 'meter_number']
    list_filter = ['customer_type', 'district']


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = ['customer', 'reading_date', 'reading_type', 'billed_consumption', 'tariff_rate']
    list_filter = ['reading_type', 'reading_date']
    search_fields = ['customer__customer_name', 'customer__account_no']
