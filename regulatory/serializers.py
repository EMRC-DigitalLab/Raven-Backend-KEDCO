from rest_framework import serializers

from .models import *


class MonthlyEnergyOfftakeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyEnergyOfftake
        fields = '__all__'


class MonthlyRevenueRecoverySerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyRevenueRecovery
        fields = '__all__'


class MonthlyUSoASubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyUSoASubmission
        fields = '__all__'


class MonthlyAPIStreamingRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyAPIStreamingRate
        fields = '__all__'


class MonthlyEstimatedBillingCappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyEstimatedBillingCapping
        fields = '__all__'


class MonthlyForumDecisionComplianceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyForumDecisionCompliance
        fields = '__all__'


class MonthlyNERCComplaintResolutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyNERCComplaintResolution
        fields = '__all__'
