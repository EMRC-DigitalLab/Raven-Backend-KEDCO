# technical/utils/explanations.py
"""
Human-readable explanations for every metric returned by the technical API.
Used by all endpoints to populate the top-level `explanations` block.
Frontend reads this block to render tooltips on dashboards.
"""

# ── Feeder list (feeders/all/) ────────────────────────────────────────────────
FEEDER_LIST = {
    "avg_hours_of_supply": (
        "Average hours per day the feeder had active electricity supply during the selected period (0–24 hrs). "
        "Calculated from HourlyLoad records where load_mw > 0."
    ),
    "duration_of_interruptions": (
        "Average hours per day the feeder was interrupted during the period (0–24 hrs). "
        "Equals 24 minus avg_hours_of_supply. Includes all interruption types."
    ),
    "turnaround_time": (
        "Average hours per day of local DisCo faults only. "
        "Load shedding (L/S) and TCN/transmission events are excluded. "
        "This is the time under the DisCo's direct control to restore supply."
    ),
    "ftc": (
        "Feeder Tripping Count — total number of distinct interruptions that started within the selected period. "
        "Each separate fault event is counted once regardless of how long it lasted."
    ),
    "energy_delivered": (
        "Total energy delivered through this feeder in the period (MWh). "
        "Source is 'meter' when valid EnergyDelivered records exist; "
        "'system' when estimated from average load × supply hours (HourlyLoad fallback); "
        "'no_data' when neither source has data for this feeder."
    ),
    "band": (
        "The NERC/MYTO service band assigned to this feeder. "
        "Band A feeders must receive ≥20 hrs/day; B ≥16; C ≥12; D ≥8; E ≥4. "
        "Determines the compliance target and the allowed daily downtime."
    ),
    "compliance_status": (
        "NERC/MYTO band compliance for this feeder in the selected period. "
        "'compliant' = avg supply ≥ band target. "
        "'at_risk' = avg supply < target but ≥ 50% of target. "
        "'critical' = avg supply < 50% of target. "
        "'no_data' = no HourlyLoad readings submitted — cannot assess compliance. "
        "'no_band' = feeder has no band assigned in the system."
    ),
    "ongoing_interruption": (
        "Live status of any unresolved fault on this feeder right now. "
        "'has_interruption: true' means there is an active fault. "
        "'breaching: true' means the fault duration has already exceeded the band's daily downtime allowance "
        "(band_allowance_hours = 24 − band target hours), meaning the feeder is actively violating its NERC commitment."
    ),
}

# ── Overview (overview/) ──────────────────────────────────────────────────────
OVERVIEW = {
    "highlight_metrics.energy_delivered": (
        "Total energy delivered across all onboarded feeders in the period (MWh). "
        "Uses the hybrid approach: meter reading per feeder where valid, "
        "system estimate (avg load × supply hours) as fallback. "
        "'delta' is the % change vs the previous equivalent period."
    ),
    "highlight_metrics.average_load": (
        "Average demand (MW) across all onboarded feeders during hours with recorded supply. "
        "Sourced from HourlyLoad records."
    ),
    "highlight_metrics.average_station_load": (
        "Average load measured at injection stations (MW). "
        "Represents the bulk power received from the grid before feeder-level distribution losses."
    ),
    "highlight_metrics.interruptions": (
        "Total number of feeder interruption events that started within the selected period, "
        "across all onboarded feeders."
    ),
    "supply_and_quality.supply_hours": (
        "Network-average hours per day feeders had active supply. "
        "Current value = average for the selected period. "
        "History shows the same metric for the 3 previous equivalent periods."
    ),
    "supply_and_quality.interruption_duration": (
        "Network-average hours per day feeders were interrupted (= 24 − supply hours). "
        "Includes all interruption types: DisCo faults, load shedding, and TCN events."
    ),
    "supply_and_quality.turnaround_time": (
        "Network-average hours per day of local DisCo faults only. "
        "Load shedding and TCN/transmission events are excluded. "
        "Measures the DisCo's own fault restoration performance."
    ),
    "supply_and_quality.avg_interruption_duration": (
        "Mean duration of individual interruption events in the period (hours per event). "
        "Lower is better — indicates faster fault restoration."
    ),
    "technical_breakdown": (
        "Count of interruptions per fault type (E/F, O/C, L/S, TCN, etc.) "
        "for the selected period. Shows which fault types are most frequent."
    ),
    "interruption_sources": (
        "4-period history of interruption counts, broken down by fault type. "
        "Period 0 = current; period 1 = one period back; etc. "
        "Use this to spot trends in fault frequency."
    ),
    "compliance": (
        "NERC/MYTO band compliance summary for all onboarded feeders with a band assigned. "
        "'compliant' = met their daily supply target. "
        "'non_compliant' = had data but fell below target. "
        "'no_data' = no HourlyLoad readings submitted — unread, not necessarily failing. "
        "'by_band' breaks these counts down per band (A → E)."
    ),
    "interruption_breakdown": (
        "4-period history of interruption counts split into three categories. "
        "'ls' = standalone load shedding (L/S only). "
        "'tcn' = transmission/grid events (TCN faults, 132KV, 330KV, T/LS, L/S GS). "
        "'disco' = all other faults under DisCo control (E/F, O/C, T/F, etc.). "
        "Each category shows: interruption_count, feeders_affected, mean_time_to_restore_hours."
    ),
}

# ── States (states/all/ and states/single/) ───────────────────────────────────
STATES = {
    "avg_hours_of_supply": (
        "Average hours per day feeders in this state had active supply during the period (0–24 hrs)."
    ),
    "duration_of_interruptions": (
        "Average hours per day feeders in this state were interrupted (= 24 − supply hours). "
        "Includes all interruption types."
    ),
    "turnaround_time": (
        "Average hours per day of local DisCo faults only within this state. "
        "Excludes load shedding and TCN/transmission events."
    ),
    "ftc": (
        "Total feeder tripping count — distinct interruptions that started in the period "
        "across all feeders in this state."
    ),
    "energy_delivered": (
        "Total energy delivered to all feeders in this state in the period (MWh). "
        "Hybrid: meter reading where valid, system estimate as fallback."
    ),
    "compliance": (
        "NERC/MYTO compliance summary scoped to this state's feeders. "
        "Counts compliant, non-compliant, and no_data feeders, broken down by band."
    ),
    "feeder_count": (
        "Number of onboarded feeders in this state included in the calculations."
    ),
}

# ── Business Districts (districts/all/ and districts/single/) ─────────────────
DISTRICTS = {
    "avg_hours_of_supply": (
        "Average hours per day feeders in this business district had active supply (0–24 hrs)."
    ),
    "duration_of_interruptions": (
        "Average hours per day feeders in this district were interrupted (= 24 − supply hours)."
    ),
    "turnaround_time": (
        "Average hours per day of local DisCo faults only within this district. "
        "Excludes load shedding and TCN/transmission events."
    ),
    "ftc": (
        "Total feeder tripping count — distinct interruptions that started in the period "
        "across all feeders in this business district."
    ),
    "energy_delivered": (
        "Total energy delivered to all feeders in this district in the period (MWh). "
        "Hybrid: meter reading where valid, system estimate as fallback."
    ),
    "compliance": (
        "NERC/MYTO compliance summary scoped to this district's feeders. "
        "Counts compliant, non-compliant, and no_data feeders, broken down by band."
    ),
    "feeder_count": (
        "Number of onboarded feeders in this district included in the calculations."
    ),
}

# ── Service Bands (service-bands/) ───────────────────────────────────────────
SERVICE_BANDS = {
    "band": (
        "The NERC/MYTO service band identifier (A–E). "
        "Band A = highest priority, guaranteed ≥20 hrs/day. "
        "Band E = lowest, guaranteed ≥4 hrs/day."
    ),
    "target_hours_per_day": (
        "NERC/MYTO minimum daily supply commitment for this band: "
        "A=20 hrs, B=16 hrs, C=12 hrs, D=8 hrs, E=4 hrs."
    ),
    "avg_hours_of_supply": (
        "Average hours per day feeders in this band actually received supply during the period."
    ),
    "compliance_rate": (
        "Percentage of feeders in this band that met their daily supply target in the period."
    ),
    "feeder_count": (
        "Number of onboarded feeders assigned to this service band."
    ),
    "energy_delivered": (
        "Total energy delivered to all feeders in this band in the period (MWh)."
    ),
    "compliance": (
        "NERC/MYTO compliance summary scoped to feeders in this service band. "
        "Counts compliant, non-compliant, and no_data feeders."
    ),
}
