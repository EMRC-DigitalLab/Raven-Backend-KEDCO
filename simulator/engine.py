from datetime import timedelta

from django.db.models import Avg, Sum

from common.models import Band, Feeder
from technical.models import FeederEnergyDaily

from .models import PCCConfig


class AllocationEngine:
    """
    Cascading energy allocation engine.

    Benchmark sources (in priority order):
      1. PCCConfig  — NERC-issued DisCo PCC figure, feeder-split proportionally
      2. FeederEnergyDaily — meter-derived demand (fallback when no PCC config exists)

    Allocation paths:
      Zone 1 (shortage)    — protect high bands, cut lower bands downward
      Zone 2 (operational) — meet all minimums, upgrade with surplus
      Zone 3 (excess)      — all feeders at maximum demand
    """

    SEVERITY_THRESHOLDS = {
        'mild':     0.75,
        'moderate': 0.50,
        'severe':   0.25,
        'critical': 0.0,
    }

    def __init__(self, simulation_date):
        self.simulation_date = simulation_date
        self.bands = list(Band.objects.order_by('priority_order'))
        self.feeders = list(
            Feeder.objects.filter(is_onboarded=True).select_related('band')
        )
        self.pcc_config = PCCConfig.get_for_date(simulation_date)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, e_offtake: float) -> dict:
        e_max, e_min, e_actual, feeder_demands = self._calculate_benchmarks()
        e_offtake = float(e_offtake)

        zone, compliance_breach, excess_supply = self._classify_zone(e_offtake, e_min, e_max)
        shortage_severity = self._shortage_severity(e_offtake, e_min) if zone == 'zone_1' else 'none'
        nerc_kpi_breach = self._check_nerc_kpi(e_offtake)

        if zone == 'zone_3':
            raw_allocations = self._allocate_excess(feeder_demands)
        elif zone == 'zone_1':
            raw_allocations = self._allocate_deficit(feeder_demands, e_offtake)
        else:
            raw_allocations = self._allocate_operational(feeder_demands, e_offtake)

        feeder_results = self._resolve_states(raw_allocations, feeder_demands)

        total_allocated = sum(r['allocated_energy_mwh'] for r in feeder_results)
        band_a_greatly_downgraded = self._check_band_a_downgrade(feeder_results, shortage_severity)
        counts = self._count_states(feeder_results)

        return {
            'e_max': e_max,
            'e_min': e_min,
            'e_actual': e_actual,
            'e_offtake': e_offtake,
            'zone': zone,
            'shortage_severity': shortage_severity,
            'compliance_breach': compliance_breach,
            'excess_supply': excess_supply,
            'nerc_kpi_breach': nerc_kpi_breach,
            'band_a_greatly_downgraded': band_a_greatly_downgraded,
            'pcc_config': self.pcc_config,
            'total_allocated_mwh': total_allocated,
            'surplus_mwh': max(0.0, e_offtake - e_min) if not compliance_breach else None,
            'deficit_mwh': max(0.0, e_min - e_offtake) if compliance_breach else None,
            'deviation_from_actual': total_allocated - e_actual,
            'deviation_from_e_min': total_allocated - e_min,
            'deviation_from_e_max': total_allocated - e_max,
            'feeder_results': feeder_results,
            **counts,
        }

    # ------------------------------------------------------------------
    # Benchmark calculation
    # ------------------------------------------------------------------

    def _calculate_benchmarks(self):
        lookback = (
            self.pcc_config.demand_lookback_days
            if self.pcc_config else 30
        )
        start = self.simulation_date - timedelta(days=lookback)

        # Step 1: calculate each feeder's average historical demand
        feeder_averages = {}
        for feeder in self.feeders:
            avg = (
                FeederEnergyDaily.objects
                .filter(feeder=feeder, date__gte=start, date__lt=self.simulation_date)
                .aggregate(avg=Avg('energy_mwh'))['avg']
            )
            feeder_averages[str(feeder.id)] = float(avg or 0)

        total_average = sum(feeder_averages.values())

        # Step 2: derive feeder demands from PCC config if available,
        # otherwise fall back to raw meter averages
        feeder_demands = {}
        if self.pcc_config and total_average > 0:
            pcc_daily = self.pcc_config.total_pcc_mwh_per_day
            for fid, avg in feeder_averages.items():
                proportion = avg / total_average
                feeder_demands[fid] = proportion * pcc_daily
        else:
            feeder_demands = feeder_averages.copy()

        # E_max = sum of feeder demands (PCC-anchored or meter-derived)
        e_max = sum(feeder_demands.values())

        # E_min = sum of (feeder demand × band minimum hours / 24)
        e_min = 0.0
        feeder_map = {str(f.id): f for f in self.feeders}
        for fid, demand in feeder_demands.items():
            feeder = feeder_map.get(fid)
            if feeder and feeder.band:
                e_min += demand * (float(feeder.band.minimum_hours) / 24)

        # E_actual = most recent available day's total from meter data
        latest = (
            FeederEnergyDaily.objects
            .filter(date__lt=self.simulation_date)
            .order_by('-date')
            .values('date')
            .first()
        )
        e_actual = 0.0
        if latest:
            e_actual = float(
                FeederEnergyDaily.objects
                .filter(date=latest['date'])
                .aggregate(total=Sum('energy_mwh'))['total'] or 0
            )

        return e_max, e_min, e_actual, feeder_demands

    # ------------------------------------------------------------------
    # Zone classification
    # ------------------------------------------------------------------

    def _classify_zone(self, e_offtake, e_min, e_max):
        if e_offtake < e_min:
            return 'zone_1', True, False
        elif e_offtake > e_max:
            return 'zone_3', False, True
        return 'zone_2', False, False

    def _shortage_severity(self, e_offtake, e_min):
        if e_min == 0:
            return 'none'
        ratio = e_offtake / e_min
        if ratio >= 0.75:
            return 'mild'
        elif ratio >= 0.50:
            return 'moderate'
        elif ratio >= 0.25:
            return 'severe'
        return 'critical'

    def _check_nerc_kpi(self, e_offtake):
        """Flag if E_offtake is below the NERC 95% offtake KPI floor."""
        if not self.pcc_config:
            return False
        return e_offtake < self.pcc_config.nerc_kpi_floor_mwh_per_day

    # ------------------------------------------------------------------
    # Allocation paths
    # ------------------------------------------------------------------

    def _allocate_operational(self, feeder_demands, e_offtake):
        """Zone 2: allocate minimums band by band, then top-up with surplus."""
        remaining = e_offtake
        allocations = {str(f.id): 0.0 for f in self.feeders}
        feeders_by_band = self._group_by_band()

        for band in self.bands:
            for feeder in feeders_by_band.get(band.name, []):
                demand = feeder_demands.get(str(feeder.id), 0)
                min_energy = demand * (float(band.minimum_hours) / 24)
                give = min(min_energy, remaining)
                allocations[str(feeder.id)] = give
                remaining -= give
                if remaining <= 0:
                    break
            if remaining <= 0:
                break

        if remaining > 0:
            for i in range(len(self.bands) - 1, 0, -1):
                current_band = self.bands[i]
                upper_band = self.bands[i - 1]
                for feeder in feeders_by_band.get(current_band.name, []):
                    if remaining <= 0:
                        break
                    demand = feeder_demands.get(str(feeder.id), 0)
                    upgrade_target = demand * (float(upper_band.minimum_hours) / 24)
                    current = allocations[str(feeder.id)]
                    top_up = max(0.0, upgrade_target - current)
                    give = min(top_up, remaining)
                    allocations[str(feeder.id)] += give
                    remaining -= give

        return allocations

    def _allocate_deficit(self, feeder_demands, e_offtake):
        """Zone 1: protect Band A first, cut lower bands."""
        remaining = e_offtake
        allocations = {str(f.id): 0.0 for f in self.feeders}
        feeders_by_band = self._group_by_band()

        for band in self.bands:
            for feeder in feeders_by_band.get(band.name, []):
                if remaining <= 0:
                    break
                demand = feeder_demands.get(str(feeder.id), 0)
                min_energy = demand * (float(band.minimum_hours) / 24)
                give = min(min_energy, remaining)
                allocations[str(feeder.id)] = give
                remaining -= give

        return allocations

    def _allocate_excess(self, feeder_demands):
        """Zone 3: every feeder gets its full demand."""
        return {str(f.id): feeder_demands.get(str(f.id), 0) for f in self.feeders}

    # ------------------------------------------------------------------
    # State resolution
    # ------------------------------------------------------------------

    def _resolve_states(self, allocations, feeder_demands):
        results = []
        for feeder in self.feeders:
            allocated = allocations.get(str(feeder.id), 0.0)
            demand = feeder_demands.get(str(feeder.id), 0.0)
            assigned_band = feeder.band

            if not assigned_band:
                continue

            effective_hours = (allocated / demand * 24) if demand > 0 else 0.0
            band_min_energy = demand * (float(assigned_band.minimum_hours) / 24)

            if allocated == 0:
                status = 'load_shed'
                effective_band = assigned_band
            elif effective_hours >= float(assigned_band.minimum_hours):
                effective_band = self._best_band_for_hours(effective_hours)
                status = (
                    'upgraded'
                    if effective_band.priority_order < assigned_band.priority_order
                    else 'energised'
                )
            else:
                status = 'downgraded'
                effective_band = self._best_band_for_hours(effective_hours) or assigned_band

            results.append({
                'feeder': feeder,
                'assigned_band': assigned_band,
                'effective_band': effective_band,
                'allocated_energy_mwh': round(allocated, 4),
                'effective_hours': round(effective_hours, 2),
                'status': status,
                'forecasted_demand_mwh': round(demand, 4),
                'band_minimum_energy_mwh': round(band_min_energy, 4),
            })

        return results

    def _best_band_for_hours(self, hours):
        for band in self.bands:
            if hours >= float(band.minimum_hours):
                return band
        return self.bands[-1] if self.bands else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _group_by_band(self):
        groups = {}
        for feeder in self.feeders:
            name = feeder.band.name if feeder.band else '__none__'
            groups.setdefault(name, []).append(feeder)
        return groups

    def _check_band_a_downgrade(self, feeder_results, shortage_severity):
        if shortage_severity not in ('severe', 'critical'):
            return False
        band_a = [r for r in feeder_results if r['assigned_band'] and r['assigned_band'].name == 'A']
        downgraded = [r for r in band_a if r['status'] in ('downgraded', 'load_shed')]
        if not band_a:
            return False
        return (len(downgraded) / len(band_a)) > 0.5

    def _count_states(self, feeder_results):
        counts = {'energised_count': 0, 'upgraded_count': 0, 'downgraded_count': 0, 'load_shed_count': 0}
        for r in feeder_results:
            key = f"{r['status']}_count"
            if key in counts:
                counts[key] += 1
        return counts
