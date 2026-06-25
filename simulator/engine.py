from datetime import timedelta

from django.db.models import Avg, Count

from commercial.models import TariffRate
from common.models import Band, Feeder
from technical.models import FeederEnergyDaily

from .models import FeederCommercialProfile, PCCConfig


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

    # Default KEDCO benchmarks (NERC Q1 2025) used when no profile exists for a feeder
    DEFAULT_BILLING_EFFICIENCY_PCT  = 99.00
    DEFAULT_COLLECTION_EFFICIENCY_PCT = 80.00

    def __init__(self, simulation_date):
        self.simulation_date = simulation_date
        self.bands = list(Band.objects.order_by('priority_order'))
        self.feeders = list(
            Feeder.objects.filter(is_onboarded=True)
            .select_related('band', 'substation', 'business_district')
        )
        self.pcc_config = PCCConfig.get_for_date(simulation_date)

        # Phase 2: commercial profiles keyed by feeder id
        self._commercial_profiles = {
            str(p.feeder_id): p
            for p in FeederCommercialProfile.objects.filter(is_active=True)
            .select_related('feeder')
        }

        # Phase 2: tariff rates keyed by band name, most recent active rate
        self._tariff_rates = {}
        for rate in TariffRate.objects.filter(is_active=True).order_by('-effective_from'):
            if rate.band not in self._tariff_rates:
                self._tariff_rates[rate.band] = float(rate.rate_per_kwh)

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
        self._apply_revenue(feeder_results)

        total_allocated = sum(r['allocated_energy_mwh'] for r in feeder_results)
        band_a_greatly_downgraded = self._check_band_a_downgrade(feeder_results, shortage_severity)
        counts = self._count_states(feeder_results)
        revenue_summary = self._summarise_revenue(feeder_results, total_allocated)

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
            'data_gap_detected': getattr(self, 'data_gap_detected', False),
            'data_reference_end': getattr(self, 'data_reference_end', None),
            'feeder_results': feeder_results,
            **counts,
            **revenue_summary,
        }

    # ------------------------------------------------------------------
    # Benchmark calculation
    # ------------------------------------------------------------------

    def _calculate_benchmarks(self):
        lookback = (
            self.pcc_config.demand_lookback_days
            if self.pcc_config else 30
        )

        # Determine the reference window.
        # Priority 1: the standard lookback window before the simulation date.
        # Priority 2: if that window is missing or has very few feeders (sparse data),
        #             find the most recent window that has adequate feeder coverage.
        # "Adequate" = at least 25% of onboarded feeders or 50, whichever is smaller.
        min_feeders = max(10, min(50, len(self.feeders) // 4))

        window_end = self.simulation_date
        standard_start = window_end - timedelta(days=lookback)

        standard_feeder_count = (
            FeederEnergyDaily.objects
            .filter(date__gte=standard_start, date__lt=window_end)
            .values('feeder').distinct().count()
        )
        has_adequate_coverage = standard_feeder_count >= min_feeders
        self.data_gap_detected = not has_adequate_coverage

        if not has_adequate_coverage:
            # Find the most recent date where enough feeders reported,
            # then build a lookback window ending there.
            dense_date_row = (
                FeederEnergyDaily.objects
                .values('date')
                .annotate(feeder_count=Count('feeder', distinct=True))
                .filter(feeder_count__gte=min_feeders, date__lt=self.simulation_date)
                .order_by('-date')
                .first()
            )
            if dense_date_row:
                window_end = dense_date_row['date'] + timedelta(days=1)
            else:
                # Last resort: anchor to most recent record regardless of coverage
                latest = (
                    FeederEnergyDaily.objects
                    .filter(date__lt=self.simulation_date)
                    .order_by('-date')
                    .values('date')
                    .first()
                )
                if latest:
                    window_end = latest['date'] + timedelta(days=1)

        start = window_end - timedelta(days=lookback)
        self.data_reference_end = window_end - timedelta(days=1)

        # Step 1: calculate each feeder's average historical demand over the
        # reference window [start, window_end).
        feeder_averages = {}
        for feeder in self.feeders:
            avg = (
                FeederEnergyDaily.objects
                .filter(feeder=feeder, date__gte=start, date__lt=window_end)
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

        # E_actual = average daily system delivery over the reference window.
        # Using the same window as feeder_averages gives a consistent baseline.
        # FeederEnergyDaily.energy_mwh is stored in kWh — divide by 1000.
        e_actual = sum(feeder_averages.values())

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
        """Zone 2: allocate minimums band by band, then exhaust surplus toward full demand."""
        remaining = e_offtake
        allocations = {str(f.id): 0.0 for f in self.feeders}
        feeders_by_band = self._group_by_band()

        # Pass 1: give each feeder its NERC band minimum, highest priority first
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

        # Pass 2: distribute surplus — upgrade lowest bands first toward full demand.
        # Iterates from Band E up to Band A, topping each feeder to its full 24h demand.
        # At E_offtake = E_max this exhausts all energy; at E_min < E_offtake < E_max
        # it produces partial upgrades, which the state resolver maps to the correct
        # effective band.
        if remaining > 0:
            for band in reversed(self.bands):
                for feeder in feeders_by_band.get(band.name, []):
                    if remaining <= 0:
                        break
                    demand = feeder_demands.get(str(feeder.id), 0)
                    current = allocations[str(feeder.id)]
                    top_up = max(0.0, demand - current)
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

    # ------------------------------------------------------------------
    # Phase 2 — Revenue calculation
    # ------------------------------------------------------------------

    def _apply_revenue(self, feeder_results):
        """
        Attach per-feeder revenue figures to each result dict in-place.

        Formula:
          revenue_potential  = allocated_mwh × 1000 × tariff_rate (₦/kWh)
          expected_billing   = revenue_potential × billing_efficiency
          expected_collection= expected_billing  × collection_efficiency
          atcc_loss          = revenue_potential − expected_collection
          revenue_per_mwh    = expected_collection ÷ allocated_mwh
        """
        for r in feeder_results:
            feeder = r['feeder']
            allocated = r['allocated_energy_mwh']
            band_name = feeder.band.name if feeder.band else None

            tariff = self._tariff_rates.get(band_name) if band_name else None
            profile = self._commercial_profiles.get(str(feeder.id))

            billing_eff = (
                float(profile.billing_efficiency_pct) / 100
                if profile else self.DEFAULT_BILLING_EFFICIENCY_PCT / 100
            )
            collection_eff = (
                float(profile.collection_efficiency_pct) / 100
                if profile else self.DEFAULT_COLLECTION_EFFICIENCY_PCT / 100
            )

            if tariff and allocated > 0:
                revenue_potential   = round(allocated * 1000 * tariff, 2)
                expected_billing    = round(revenue_potential * billing_eff, 2)
                expected_collection = round(expected_billing * collection_eff, 2)
                atcc_loss           = round(revenue_potential - expected_collection, 2)
                rev_per_mwh         = round(expected_collection / allocated, 2)
            else:
                revenue_potential = expected_billing = expected_collection = atcc_loss = rev_per_mwh = None

            r.update({
                'tariff_rate_ngn_per_kwh':    tariff,
                'billing_efficiency_pct':      round(billing_eff * 100, 2),
                'collection_efficiency_pct':   round(collection_eff * 100, 2),
                'revenue_potential_ngn':       revenue_potential,
                'expected_billing_ngn':        expected_billing,
                'expected_collection_ngn':     expected_collection,
                'atcc_loss_ngn':               atcc_loss,
                'revenue_per_mwh_ngn':         rev_per_mwh,
            })

    def _summarise_revenue(self, feeder_results, total_allocated_mwh):
        """Roll up per-feeder revenue to simulation-level totals."""
        total_potential   = sum(r['revenue_potential_ngn']   or 0 for r in feeder_results)
        total_billing     = sum(r['expected_billing_ngn']     or 0 for r in feeder_results)
        total_collection  = sum(r['expected_collection_ngn']  or 0 for r in feeder_results)
        total_atcc        = sum(r['atcc_loss_ngn']            or 0 for r in feeder_results)
        rev_per_mwh = (
            round(total_collection / total_allocated_mwh, 2)
            if total_allocated_mwh > 0 else None
        )
        return {
            'total_revenue_potential_ngn':  round(total_potential, 2),
            'total_expected_billing_ngn':   round(total_billing, 2),
            'total_expected_collection_ngn': round(total_collection, 2),
            'total_atcc_loss_ngn':          round(total_atcc, 2),
            'revenue_per_mwh_ngn':          rev_per_mwh,
        }
