from rest_framework import serializers

from .models import *


class OpexCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OpexCategory
        fields = '__all__'

class GLBreakdownSerializer(serializers.ModelSerializer):
    class Meta:
        model = GLBreakdown
        fields = '__all__'

class OpexSerializer(serializers.ModelSerializer):
    class Meta:
        model = Opex
        fields = '__all__'

class HQOpexSerializer(serializers.ModelSerializer):
    class Meta:
        model = HQOpex
        fields = '__all__'


class MonthlyRevenueBilledSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyRevenueBilled
        fields = '__all__'

class SalaryPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryPayment
        fields = [
            "id", "district", "month", "staff", 
            "payment_date", "amount", "created_at"
        ]

