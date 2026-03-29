# KEDCO Raven — Technical Module: All Additions
### Frontend Integration Guide

**Base URL (staging):** `https://staging.apiraven.raven-emrc.com/api/technical/`
**Auth:** Bearer token in `Authorization` header
**All responses:** `application/json`

> This document covers everything added to existing technical endpoints.
> No new endpoints were created — all data is embedded in the existing responses.

---

## Summary of Everything Added

| Feature | Where |
|---|---|
| Per-feeder band + compliance status + ongoing interruption | `feeders/all/` |
| Energy delivered per feeder (hybrid) | `feeders/all/` |
| Sort feeders Band A → E | `feeders/all/` |
| `?interruption_type=` filter | `feeders/all/` |
| Compliance summary block | overview, states, districts, service-bands |
| Interruption breakdown (ls / tcn / disco) — 4 periods | `overview/` only |
| Customer count (MDI / MDNI / feeder_count) | `overview/` technical_breakdown |
| `explanations` tooltip block | all endpoints |
| Fault type category admin (dynamic ls/tcn mapping) | Django Admin |

---

## 1. Feeders List — `GET /api/technical/feeders/all/`

### Query Parameters

| Param | Values | Description |
|---|---|---|
| `feeder_type` | `11kv` `33kv` | Filter by voltage level |
| `state` | state name | Filter by state |
| `business_district` | district name | Filter by business district |
| `interruption_type` | `ls` `tcn` `disco` | Only feeders that had that interruption category in the period |
| `mode` | `monthly` `daily` `custom` etc. | Date range mode |

> `interruption_type` and `feeder_type` stack — e.g. `?interruption_type=disco&feeder_type=11kv` returns only 11kv feeders that had a DisCo fault.

### Response shape

```json
{
  "explanations": { ... },
  "feeders": [
    {
      "feeder_name": "COCA COLA",
      "feeder_slug": "kn-tam-coc",
      "voltage_level": "11kv",
      "substation_name": "TAMBURAWA",
      "avg_hours_of_supply": 18.5,
      "duration_of_interruptions": 5.5,
      "turnaround_time": 1.5,
      "ftc": 12,

      "energy_delivered": {
        "mwh": 142.5,
        "source": "meter"
      },

      "band": {
        "slug": "a",
        "name": "A",
        "target_hours_per_day": 20
      },

      "compliance_status": "at_risk",

      "ongoing_interruption": {
        "has_interruption": true,
        "type": "E/F",
        "duration_hours": 6.5,
        "band_allowance_hours": 4.0,
        "breaching": true
      }
    }
  ]
}
```

### Sort order

Feeders are returned **Band A first → B → C → D → E**, then alphabetically within each band. No sort parameter needed.

### `energy_delivered` object

| Field | Description |
|---|---|
| `mwh` | Total MWh delivered to this feeder in the period |
| `source` | `meter` = real EnergyDelivered record; `system` = estimated from avg load × supply hours; `no_data` = no data available |

### `band` object

| Field | Description |
|---|---|
| `slug` | `a` `b` `c` `d` `e` |
| `name` | `A` `B` `C` `D` `E` |
| `target_hours_per_day` | NERC minimum hrs/day (A=20, B=16, C=12, D=8, E=4) |

### `compliance_status` values

| Value | Meaning |
|---|---|
| `compliant` | Avg supply ≥ band target |
| `at_risk` | Avg supply < target but ≥ 50% of target |
| `critical` | Avg supply < 50% of target |
| `no_data` | No HourlyLoad records submitted — unknown state, **not** a failure |
| `no_band` | No band assigned in the system |

### `ongoing_interruption` object

| Field | Type | Description |
|---|---|---|
| `has_interruption` | bool | `true` if there is an active unresolved fault right now |
| `type` | string | Fault type e.g. `E/F`, `O/C`, `L/S` |
| `duration_hours` | float | How long the fault has been running |
| `band_allowance_hours` | float | Max downtime the band permits per day = `24 − target_hours` |
| `breaching` | bool | `true` when duration already exceeds the band's daily allowance — NERC violation in progress |

**Band downtime allowance:**
| Band | Target hrs | Max downtime |
|---|---|---|
| A | 20 hrs | **4 hrs** |
| B | 16 hrs | **8 hrs** |
| C | 12 hrs | **12 hrs** |
| D | 8 hrs | **16 hrs** |
| E | 4 hrs | **20 hrs** |

---

## 2. Overview — `GET /api/technical/overview/`

Three blocks are added to the existing response.

### 2a. `compliance` block

```json
"compliance": {
  "total_feeders": 100,
  "compliant": 65,
  "non_compliant": 25,
  "no_data": 10,
  "by_band": [
    { "slug": "a", "name": "A", "compliant": 20, "non_compliant": 8, "no_data": 2 },
    { "slug": "b", "name": "B", "compliant": 18, "non_compliant": 4, "no_data": 3 },
    { "slug": "c", "name": "C", "compliant": 15, "non_compliant": 6, "no_data": 2 },
    { "slug": "d", "name": "D", "compliant": 8,  "non_compliant": 5, "no_data": 2 },
    { "slug": "e", "name": "E", "compliant": 4,  "non_compliant": 2, "no_data": 1 }
  ]
}
```

> `compliant + non_compliant + no_data = total_feeders`

### 2b. `interruption_sources` — updated shape

4-period array. Each period's `breakdown` is now grouped by **ls / tcn / disco** instead of raw fault codes.

```json
"interruption_sources": [
  {
    "month": "Cycle 1",
    "total": 1942,
    "delta": 0,
    "breakdown": {
      "ls": {
        "total": 1500,
        "codes": { "L/S": 1500 }
      },
      "tcn": {
        "total": 322,
        "codes": {
          "330KV L/F": 18,
          "132KV L/F": 7,
          "330KV L/S": 40,
          "tcn": 90,
          "132KV E/F": 3,
          "132KV CB/F": 17,
          "132KV MTCE": 71,
          "L/S GS": 76
        }
      },
      "disco": {
        "total": 120,
        "codes": {
          "E/F": 19,
          "O/S": 46,
          "fault": 3,
          "B/F": 3,
          "EM/D": 3,
          "OFF": 6,
          "permit": 13,
          "MTCE": 7
        }
      }
    }
  },
  { ... },
  { ... },
  { ... }
]
```

`breakdown.ls.total + breakdown.tcn.total + breakdown.disco.total = total` always.

**`codes`** — raw fault code counts within each category. Use this to drill down into what specific faults are driving the category total.

### 2c. `interruption_breakdown` block

4-period array. Each period has 3 category objects with aggregate stats (count, feeders, MTTR). Use this for the high-level chart — `interruption_sources` for the drill-down.

```json
"interruption_breakdown": [
  {
    "ls":    { "interruption_count": 1500, "feeders_affected": 73,  "mean_time_to_restore_hours": 11.31 },
    "tcn":   { "interruption_count": 322,  "feeders_affected": 54,  "mean_time_to_restore_hours": 6.65 },
    "disco": { "interruption_count": 120,  "feeders_affected": 34,  "mean_time_to_restore_hours": 10.41 }
  },
  { ... },
  { ... },
  { ... }
]
```

**Categories:**

| Key | What it includes |
|---|---|
| `ls` | Only standalone `L/S` (system-wide load shedding) |
| `tcn` | TCN/grid events: `TCN`, `132KV E/F`, `132KV CB/F`, `132KV MTCE`, `132KV L/F`, `330KV L/F`, `330KV L/S`, `T/LS`, `L/S GS` |
| `disco` | Everything else — local DisCo faults under the distribution company's control |

**Per category (`interruption_breakdown`):**

| Field | Description |
|---|---|
| `interruption_count` | Total interruptions that started in the period |
| `feeders_affected` | Distinct feeders with at least one interruption of this type |
| `mean_time_to_restore_hours` | Avg restore time for resolved interruptions (0 if none resolved yet) |

> **Click-through to feeder list:** `GET /api/technical/feeders/all/?interruption_type=disco&feeder_type=11kv`

> **Category mapping is admin-managed** — go to Django Admin → *Technical → Fault Type Categories* to add, remove, or reassign fault codes. Changes take effect within 60 seconds. Any code not explicitly mapped is automatically classified as `disco`.

### 2c. `technical_breakdown.customer_count` — updated

Previously hardcoded to 0. Now returns live counts:

```json
"technical_breakdown": {
  "feeder_count": { "value": 87, "delta": 0 },
  "interruption_count": { "value": 55, "delta": 12.5 },
  "avg_turnaround": { "value": 2.1, "delta": -5.0 },
  "customer_count": {
    "total": 1250,
    "mdi": 450,
    "mdni": 800,
    "feeder_count": 87
  }
}
```

| Field | Description |
|---|---|
| `total` | All MDI + MDNI customers on onboarded feeders |
| `mdi` | Maximum Demand Installation customers |
| `mdni` | Non-Maximum Demand customers |
| `feeder_count` | Distinct feeders that have at least one customer assigned |

---

## 3. Compliance Summary Block

Added to: **overview**, **all states**, **single state**, **all districts**, **single district**, **service-bands**.

Same shape on all endpoints — scoped to that endpoint's feeders.

| Endpoint | Scoped to |
|---|---|
| `GET /api/technical/overview/` | Whole network |
| `GET /api/technical/states/all/` | Whole network |
| `GET /api/technical/states/single/?state=Kano` | That state |
| `GET /api/technical/business-districts/all/?state=Kano` | That state |
| `GET /api/technical/business-districts/single/?district=Kano+Industrial` | That district |
| `GET /api/technical/service-bands/` | Whole network |

All endpoints respect `feeder_type=11kv` or `feeder_type=33kv`.

---

## 4. `explanations` Block — All Endpoints

Every technical endpoint now returns a top-level `explanations` object. Use it to drive tooltips on the frontend — no hardcoding needed.

```json
"explanations": {
  "avg_hours_of_supply": "Average hours per day the feeder had active supply...",
  "turnaround_time": "Average hours per day of local DisCo faults only...",
  "compliance_status": "NERC/MYTO band compliance: compliant, at_risk, critical...",
  "energy_delivered": "Total MWh delivered. Source: meter if available, otherwise system estimate...",
  ...
}
```

Read `response.explanations["field_name"]` for any tooltip text. All strings are maintained in one place: `technical/utils/explanations.py`.

---

## UI Implementation Notes

### Compliance status colours
```js
const COMPLIANCE_COLORS = {
  compliant:    '#22c55e',  // green
  at_risk:      '#f59e0b',  // amber
  critical:     '#ef4444',  // red
  no_data:      '#94a3b8',  // grey — unknown, not a failure
  no_band:      '#e2e8f0',  // light grey — system config issue
}
```

### Ongoing interruption badge
- `has_interruption: false` → no badge
- `has_interruption: true, breaching: false` → amber: *"Fault — X hrs (within allowance)"*
- `has_interruption: true, breaching: true` → red: *"BREACHING — X hrs (band allows Y hrs)"*

### Interruption breakdown colours
```js
const INTERRUPTION_CATEGORY_COLORS = {
  ls:    '#f59e0b',  // amber — planned, not a DisCo failure
  tcn:   '#3b82f6',  // blue  — grid/transmission, outside DisCo control
  disco: '#ef4444',  // red   — local fault, DisCo responsible
}
```

### Compliance summary display
- **Compliant** → green
- **Non-compliant** → red
- **No data** → grey (unread, not failing — show separately)

`by_band` array drives the band breakdown table — always render Band A first.

---

*KEDCO Raven Technical Module — All additions to existing endpoints.*
