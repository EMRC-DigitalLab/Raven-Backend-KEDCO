from rest_framework import serializers

from common.models import DistributionTransformer
from common.serializers import FeederSerializer

from .models import *


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = '__all__'


class MonthlyRevenueBilledSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyRevenueBilled
        fields = '__all__'


class MonthlyEnergyBilledSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyEnergyBilled
        fields = '__all__'


class MonthlyCustomerStatsSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyCustomerStats
        fields = '__all__'

class TransformerDetailSerializer(serializers.ModelSerializer):
    """Serializer for transformer details in sales rep response"""
    class Meta:
        model = DistributionTransformer
        fields = ['id', 'name', 'slug']

class SalesRepresentativeSerializer(serializers.ModelSerializer):
    assigned_transformers = TransformerDetailSerializer(many=True, read_only=True)

    class Meta:
        model = SalesRepresentative
        fields = '__all__'


class SalesRepPerformanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesRepPerformance
        fields = '__all__'


class DailyCollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyCollection
        fields = '__all__'


class OverviewMetricSerializer(serializers.Serializer):
    month = serializers.CharField()

    billing_efficiency = serializers.FloatField()
    collection_efficiency = serializers.FloatField()
    atcc = serializers.FloatField()

    revenue_billed = serializers.FloatField()
    revenue_collected = serializers.FloatField()
    energy_billed = serializers.FloatField()
    energy_delivered = serializers.FloatField()

    customer_response_rate = serializers.FloatField()
    total_cost = serializers.FloatField()

    delta_atcc = serializers.FloatField(required=False)
    delta_billing_efficiency = serializers.FloatField(required=False)
    delta_collection_efficiency = serializers.FloatField(required=False)
    
