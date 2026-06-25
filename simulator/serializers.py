from rest_framework import serializers

from .models import PCCConfig, SimulationFeederResult, SimulationRun


class PCCConfigSerializer(serializers.ModelSerializer):
    total_pcc_mwh_per_day = serializers.FloatField(read_only=True)
    nerc_kpi_floor_mwh_per_day = serializers.FloatField(read_only=True)

    class Meta:
        model = PCCConfig
        fields = [
            'id', 'label', 'total_pcc_mwh_per_hour', 'total_pcc_mwh_per_day',
            'nerc_offtake_kpi_pct', 'nerc_kpi_floor_mwh_per_day',
            'demand_lookback_days', 'effective_from', 'effective_to',
            'is_active', 'source',
        ]


class SimulationRunRequestSerializer(serializers.Serializer):
    scenario = serializers.ChoiceField(choices=SimulationRun.SCENARIO_CHOICES)
    simulation_date = serializers.DateField()
    e_offtake = serializers.DecimalField(
        max_digits=14, decimal_places=4, required=False, allow_null=True,
        help_text="Required for SBT Ration scenario. Auto-calculated for others."
    )

    def validate(self, data):
        if data['scenario'] == 'sbt_ration' and not data.get('e_offtake'):
            raise serializers.ValidationError(
                {"e_offtake": "e_offtake is required for the SBT Ration scenario."}
            )
        return data


class BandSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    minimum_hours = serializers.DecimalField(max_digits=4, decimal_places=1)
    priority_order = serializers.IntegerField()


class FeederResultSerializer(serializers.ModelSerializer):
    feeder_id = serializers.UUIDField(source='feeder.id')
    feeder_name = serializers.CharField(source='feeder.name')
    voltage_level = serializers.CharField(source='feeder.voltage_level')
    feeder_class = serializers.CharField(source='feeder.feeder_class')
    substation = serializers.CharField(source='feeder.substation.name')
    business_district = serializers.SerializerMethodField()
    assigned_band = BandSummarySerializer()
    effective_band = BandSummarySerializer()

    class Meta:
        model = SimulationFeederResult
        fields = [
            'feeder_id', 'feeder_name',
            'voltage_level', 'feeder_class',
            'substation', 'business_district',
            'assigned_band', 'effective_band',
            'allocated_energy_mwh', 'effective_hours',
            'status',
            'forecasted_demand_mwh', 'band_minimum_energy_mwh',
        ]

    def get_business_district(self, obj):
        return obj.feeder.business_district.name if obj.feeder.business_district else None


class SimulationRunSerializer(serializers.ModelSerializer):
    feeder_results = FeederResultSerializer(many=True, read_only=True)
    scenario_display = serializers.CharField(source='get_scenario_display', read_only=True)
    zone_display = serializers.CharField(source='get_zone_display', read_only=True)
    pcc_config = PCCConfigSerializer(read_only=True)

    class Meta:
        model = SimulationRun
        fields = [
            'id', 'scenario', 'scenario_display',
            'simulation_date',
            'e_offtake', 'e_min', 'e_max', 'e_actual',
            'zone', 'zone_display', 'shortage_severity',
            'compliance_breach', 'excess_supply', 'band_a_greatly_downgraded',
            'nerc_kpi_breach', 'pcc_config',
            'total_allocated_mwh', 'surplus_mwh', 'deficit_mwh',
            'deviation_from_actual', 'deviation_from_e_min', 'deviation_from_e_max',
            'energised_count', 'upgraded_count', 'downgraded_count', 'load_shed_count',
            'created_at',
            'feeder_results',
        ]


class SimulationRunListSerializer(serializers.ModelSerializer):
    scenario_display = serializers.CharField(source='get_scenario_display', read_only=True)
    zone_display = serializers.CharField(source='get_zone_display', read_only=True)

    class Meta:
        model = SimulationRun
        fields = [
            'id', 'scenario', 'scenario_display',
            'simulation_date',
            'e_offtake', 'e_min', 'e_max', 'e_actual',
            'zone', 'zone_display',
            'compliance_breach', 'excess_supply',
            'total_allocated_mwh',
            'energised_count', 'upgraded_count', 'downgraded_count', 'load_shed_count',
            'created_at',
        ]


class BenchmarkSerializer(serializers.Serializer):
    simulation_date = serializers.DateField()
    e_min = serializers.FloatField()
    e_max = serializers.FloatField()
    e_actual = serializers.FloatField()
    feeder_count = serializers.IntegerField()
    data_gap_detected = serializers.BooleanField()
    data_reference_end = serializers.DateField(allow_null=True)
