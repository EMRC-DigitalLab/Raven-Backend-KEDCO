from datetime import date

from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import AllocationEngine
from .models import SimulationFeederResult, SimulationRun
from .serializers import (
    BenchmarkSerializer,
    SimulationRunListSerializer,
    SimulationRunRequestSerializer,
    SimulationRunSerializer,
)


class SimulationRunView(APIView):
    """
    POST /api/simulator/run/
    Run a simulation and persist the results.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        req = SimulationRunRequestSerializer(data=request.data)
        if not req.is_valid():
            return Response(req.errors, status=status.HTTP_400_BAD_REQUEST)

        data = req.validated_data
        scenario = data['scenario']
        sim_date = data['simulation_date']

        engine = AllocationEngine(simulation_date=sim_date)

        # Pre-calculate benchmarks once to resolve scenario-based offtake,
        # then run() re-uses the engine's cached state.
        e_max, e_min, e_actual, _ = engine._calculate_benchmarks()

        scenario_offtake = {
            'actual':       e_actual,
            'minimum_sbt':  e_min,
            'maximum_sbt':  e_max,
            'unsuppressed': e_max,
        }

        # For sbt_ration the user enters MWh/h — multiply by 24 to get MWh/day.
        # All other scenarios use pre-computed MWh/day benchmarks.
        if scenario == 'sbt_ration' and data.get('e_offtake'):
            e_offtake = float(data['e_offtake']) * 24
        else:
            e_offtake = float(scenario_offtake.get(scenario, e_actual))

        result = engine.run(e_offtake)

        sim = SimulationRun.objects.create(
            scenario=scenario,
            simulation_date=sim_date,
            e_offtake=result['e_offtake'],
            e_min=result['e_min'],
            e_max=result['e_max'],
            e_actual=result['e_actual'],
            zone=result['zone'],
            shortage_severity=result['shortage_severity'],
            compliance_breach=result['compliance_breach'],
            excess_supply=result['excess_supply'],
            band_a_greatly_downgraded=result['band_a_greatly_downgraded'],
            nerc_kpi_breach=result['nerc_kpi_breach'],
            pcc_config=result['pcc_config'],
            total_allocated_mwh=result['total_allocated_mwh'],
            surplus_mwh=result['surplus_mwh'],
            deficit_mwh=result['deficit_mwh'],
            deviation_from_actual=result['deviation_from_actual'],
            deviation_from_e_min=result['deviation_from_e_min'],
            deviation_from_e_max=result['deviation_from_e_max'],
            energised_count=result['energised_count'],
            upgraded_count=result['upgraded_count'],
            downgraded_count=result['downgraded_count'],
            load_shed_count=result['load_shed_count'],
            # Phase 2 — revenue summary
            total_revenue_potential_ngn=result.get('total_revenue_potential_ngn'),
            total_expected_billing_ngn=result.get('total_expected_billing_ngn'),
            total_expected_collection_ngn=result.get('total_expected_collection_ngn'),
            total_atcc_loss_ngn=result.get('total_atcc_loss_ngn'),
            revenue_per_mwh_ngn=result.get('revenue_per_mwh_ngn'),
            created_by=request.user,
        )

        SimulationFeederResult.objects.bulk_create([
            SimulationFeederResult(
                simulation=sim,
                feeder=r['feeder'],
                assigned_band=r['assigned_band'],
                effective_band=r['effective_band'],
                allocated_energy_mwh=r['allocated_energy_mwh'],
                effective_hours=r['effective_hours'],
                status=r['status'],
                forecasted_demand_mwh=r['forecasted_demand_mwh'],
                band_minimum_energy_mwh=r['band_minimum_energy_mwh'],
                # Phase 2 — per-feeder revenue
                tariff_rate_ngn_per_kwh=r.get('tariff_rate_ngn_per_kwh'),
                billing_efficiency_pct=r.get('billing_efficiency_pct'),
                collection_efficiency_pct=r.get('collection_efficiency_pct'),
                revenue_potential_ngn=r.get('revenue_potential_ngn'),
                expected_billing_ngn=r.get('expected_billing_ngn'),
                expected_collection_ngn=r.get('expected_collection_ngn'),
                atcc_loss_ngn=r.get('atcc_loss_ngn'),
                revenue_per_mwh_ngn=r.get('revenue_per_mwh_ngn'),
            )
            for r in result['feeder_results']
        ])

        serializer = SimulationRunSerializer(sim)
        response_data = dict(serializer.data)
        response_data['data_gap_detected'] = result.get('data_gap_detected', False)
        response_data['data_reference_end'] = (
            str(result['data_reference_end']) if result.get('data_reference_end') else None
        )
        return Response(response_data, status=status.HTTP_201_CREATED)


class SimulationRunListView(ListAPIView):
    """
    GET /api/simulator/runs/
    List all past simulation runs (no feeder detail).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SimulationRunListSerializer

    def get_queryset(self):
        qs = SimulationRun.objects.all()
        scenario = self.request.query_params.get('scenario')
        if scenario:
            qs = qs.filter(scenario=scenario)
        return qs


class SimulationRunDetailView(RetrieveAPIView):
    """
    GET /api/simulator/runs/{id}/
    Full simulation result including per-feeder breakdown.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SimulationRunSerializer
    queryset = SimulationRun.objects.prefetch_related(
        'feeder_results__feeder__substation',
        'feeder_results__feeder__business_district',
        'feeder_results__assigned_band',
        'feeder_results__effective_band',
    )


class MethodologyView(APIView):
    """
    GET /api/simulator/methodology/
    Returns the full simulator methodology as structured JSON.
    Frontend uses this to populate tooltips, info panels and help sections.
    No auth required — read-only reference data.
    """
    permission_classes = []

    def get(self, request):
        return Response({
            "title": "Raven Predictive Simulator — Methodology",
            "overview": (
                "The simulator takes one input — your energy offtake in MWh/h — "
                "and produces a complete allocation picture across every feeder in the network. "
                "The system converts your MWh/h figure to a daily MWh total automatically. "
                "You never need to calculate the 24-hour conversion yourself."
            ),
            "input": {
                "label": "Energy Offtake (MWh/h)",
                "description": (
                    "Enter the figure the System Operator has confirmed for the period. "
                    "This is your Partially Contracted Capacity (PCC) nomination — "
                    "the energy KEDCO has declared to offtake from the grid. "
                    "The system multiplies by 24 to derive the daily MWh total."
                ),
            },
            "benchmarks": [
                {
                    "key": "E_max",
                    "label": "Network Maximum (Ceiling)",
                    "description": (
                        "The total energy KEDCO has contracted to receive under its PCC agreement with NERC. "
                        "At this level every feeder is fully served. "
                        "Calculated from the active PCC configuration and each feeder's historical demand proportion."
                    ),
                },
                {
                    "key": "E_min",
                    "label": "NERC Compliance Floor",
                    "description": (
                        "The minimum energy required to keep every feeder at its NERC-mandated "
                        "minimum service hours for its assigned band. "
                        "Falling below this is a regulatory compliance breach. "
                        "Calculated from each feeder's forecasted demand multiplied by its band minimum hours."
                    ),
                },
                {
                    "key": "E_actual",
                    "label": "Ground Truth",
                    "description": (
                        "The total energy actually delivered across the network on the most recent recorded day. "
                        "Every simulation result is compared against this to show deviation from real-world performance."
                    ),
                },
            ],
            "zones": [
                {
                    "key": "zone_1",
                    "label": "Zone 1 — Critical Shortage",
                    "condition": "E_offtake < E_min",
                    "description": "Available energy is below the NERC compliance floor. Allocation runs in deficit mode. Lower bands are cut to protect higher bands.",
                    "severity_levels": [
                        {"range": "75–99% of E_min", "label": "Mild", "description": "Lower bands receive nothing. Band A mostly protected."},
                        {"range": "50–74% of E_min", "label": "Moderate", "description": "Band A feeders begin to experience downgrade."},
                        {"range": "25–49% of E_min", "label": "Severe", "description": "Majority of Band A feeders receive reduced supply."},
                        {"range": "< 25% of E_min",  "label": "Critical", "description": "System-wide load shedding. Most feeders enter L/S regardless of band."},
                    ],
                },
                {
                    "key": "zone_2",
                    "label": "Zone 2 — Operational",
                    "condition": "E_min ≤ E_offtake ≤ E_max",
                    "description": "Normal operating zone. All feeders can be served at minimum. Surplus energy triggers upgrades from lower bands upward.",
                },
                {
                    "key": "zone_3",
                    "label": "Zone 3 — Excess Supply",
                    "condition": "E_offtake > E_max",
                    "description": "Available energy exceeds the network's total contracted capacity. All feeders run at maximum. Excess is reported but cannot be utilised.",
                },
            ],
            "allocation_logic": {
                "description": (
                    "Energy is allocated by band priority — Band A first, then B, C, D, E. "
                    "In shortage, the system protects high bands and cuts low bands. "
                    "In surplus, the system meets all minimums first then upgrades lower bands upward."
                ),
                "upgrade_rule": "A feeder is upgraded when its allocated energy meets or exceeds the minimum threshold of the next higher band.",
                "downgrade_rule": "A feeder is downgraded when its allocated energy is greater than zero but less than its assigned band minimum.",
            },
            "feeder_states": [
                {"key": "energised",  "label": "Energised",  "description": "Receiving its full NERC minimum. Compliant at assigned band."},
                {"key": "upgraded",   "label": "Upgraded",   "description": "Receiving more than its minimum. Effective band moves up. A surplus outcome."},
                {"key": "downgraded", "label": "Downgraded", "description": "Receiving less than its minimum. Compliance breach at feeder level. Effective band drops."},
                {"key": "load_shed",  "label": "L/S",        "description": "Load Shed. Feeder receives nothing. Band assignment retained for reference."},
            ],
            "nerc_bands": [
                {"band": "A", "minimum_hours": 20, "priority": 1, "description": "Highest priority. Served first, cut last."},
                {"band": "B", "minimum_hours": 16, "priority": 2, "description": "Second priority."},
                {"band": "C", "minimum_hours": 12, "priority": 3, "description": "Third priority."},
                {"band": "D", "minimum_hours": 8,  "priority": 4, "description": "Fourth priority."},
                {"band": "E", "minimum_hours": 4,  "priority": 5, "description": "Lowest priority. First to be cut in shortage."},
            ],
            "flags": [
                {
                    "key": "compliance_breach",
                    "label": "Compliance Breach",
                    "description": "E_offtake has fallen below E_min. The network cannot meet NERC minimum service hours at current supply.",
                },
                {
                    "key": "nerc_kpi_breach",
                    "label": "NERC Offtake KPI Breach",
                    "description": "E_offtake is below 95% of KEDCO's available PCC nomination. NERC penalty risk applies.",
                },
                {
                    "key": "excess_supply",
                    "label": "Excess Supply",
                    "description": "E_offtake exceeds the network maximum. Surplus energy cannot be absorbed by the network.",
                },
                {
                    "key": "band_a_greatly_downgraded",
                    "label": "Band A Greatly Downgraded",
                    "description": "More than 50% of Band A feeders are receiving less than their minimum supply. Critical alert for priority customers.",
                },
            ],
            "scenarios": [
                {"key": "actual",       "label": "Actual",        "description": "Uses the most recent real energy delivered as E_offtake. Ground truth benchmark."},
                {"key": "minimum_sbt",  "label": "Minimum SBT",   "description": "Sets E_offtake to E_min. Worst-case compliant scenario."},
                {"key": "maximum_sbt",  "label": "Maximum SBT",   "description": "Sets E_offtake to E_max. Best-case full supply scenario."},
                {"key": "sbt_ration",   "label": "SBT Ration",    "description": "User-defined E_offtake. Use for operational what-if planning at any supply level."},
                {"key": "unsuppressed", "label": "Unsuppressed",   "description": "Sets E_offtake to E_max with no load shedding applied. Shows true network demand."},
            ],
        })


class BenchmarkView(APIView):
    """
    GET /api/simulator/benchmarks/?date=YYYY-MM-DD
    Returns E_min, E_max and E_actual for a given date so the
    frontend can show the user the context before they run a simulation.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_str = request.query_params.get('date')
        try:
            sim_date = date.fromisoformat(date_str) if date_str else date.today()
        except ValueError:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        engine = AllocationEngine(simulation_date=sim_date)
        e_max, e_min, e_actual, feeder_demands = engine._calculate_benchmarks()

        payload = {
            'simulation_date': sim_date,
            'e_min': e_min,
            'e_max': e_max,
            'e_actual': e_actual,
            'feeder_count': len(feeder_demands),
            'data_gap_detected': getattr(engine, 'data_gap_detected', False),
            'data_reference_end': getattr(engine, 'data_reference_end', None),
        }
        serializer = BenchmarkSerializer(payload)
        return Response(serializer.data)
