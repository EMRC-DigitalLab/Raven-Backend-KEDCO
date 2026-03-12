from rest_framework import serializers

from .models import *


class StateSerializer(serializers.ModelSerializer):
    class Meta:
        model = State
        fields = '__all__'

class BusinessDistrictSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessDistrict
        fields = '__all__'

class InjectionSubstationSerializer(serializers.ModelSerializer):
    class Meta:
        model = InjectionSubstation
        fields = '__all__'

class FeederSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feeder
        fields = '__all__'

class DistributionTransformerSerializer(serializers.ModelSerializer):
    class Meta:
        model = DistributionTransformer
        fields = '__all__'

class BandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Band
        fields = '__all__'
