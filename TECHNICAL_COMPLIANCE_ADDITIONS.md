# KEDCO Raven — Technical Module: Compliance Additions
### Frontend Integration Guide

**Base URL (staging):** `https://staging.apiraven.raven-emrc.com/api/technical/`
**Auth:** Bearer token in `Authorization` header`
**All responses:** `application/json`

> This document covers compliance fields added to **existing** technical endpoints.
> No new endpoints were created — compliance data is embedded in the existing responses.

---

## What Was Added

Two things are now attached to existing technical endpoints:

1. **Per-feeder compliance** — on `feeders/all/`, each feeder now carries its band, compliance status, and whether it has an ongoing interruption that is breaching its band allowance.

2. **Compliance summary** — on overview, states, districts, and service-bands, a `compliance` block is appended showing total compliant / non-compliant / no_data feeder counts, broken down by band.

---

## 1. Feeders List — `GET /api/technical/feeders/all/`

Existing fields are unchanged. Three new fields are appended to every feeder:

```
GET /api/technical/feeders/all/
GET /api/technical/feeders/all/?feeder_type=11kv
GET /api/technical/feeders/all/?feeder_type=33kv&state=Kano
```

### New fields per feeder

```json
{
  "feeder_name": "COCA COLA",
  "feeder_slug": "kn-tam-coc",
  "voltage_level": "11kv",
  "substation_name": "TAMBURAWA",
  "avg_hours_of_supply": 18.5,
  "duration_of_interruptions": 5.5,
  "turnaround_time": 1.5,
  "ftc": 12,

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
```

### `band` object

| Field | Description |
|---|---|
| `slug` | `a` `b` `c` `d` `e` |
| `name` | `A` `B` `C` `D` `E` |
| `target_hours_per_day` | NERC minimum hrs/day for this band (A=20, B=16, C=12, D=8, E=4) |

### `compliance_status` values

| Value | Meaning |
|---|---|
| `compliant` | Avg hours supplied ≥ band target for the selected period |
| `at_risk` | Avg hours < target but ≥ 50% of target |
| `critical` | Avg hours < 50% of target |
| `no_data` | Zero HourlyLoad records submitted for this feeder in the period — cannot determine compliance |
| `no_band` | Feeder has no band assigned in the system |

> `no_data` means the field team has not submitted any readings for this feeder in the selected period. It is **not** the same as zero supply — treat it as an unread/unknown state, not a failure.

### `ongoing_interruption` object

| Field | Type | Description |
|---|---|---|
| `has_interruption` | bool | `true` if there is an unresolved interruption right now |
| `type` | string | Interruption type e.g. `E/F`, `O/C`, `L/S` |
| `duration_hours` | float | How long the interruption has been running (hours) |
| `band_allowance_hours` | float | Max downtime the band permits per day = `24 − target_hours` |
| `breaching` | bool | `true` when `duration_hours > band_allowance_hours` — feeder is actively violating its NERC commitment right now |

**Band allowance reference:**
| Band | Target hrs | Max downtime allowed |
|---|---|---|
| A | 20 hrs | **4 hrs** |
| B | 16 hrs | **8 hrs** |
| C | 12 hrs | **12 hrs** |
| D | 8 hrs | **16 hrs** |
| E | 4 hrs | **20 hrs** |

> `breaching: true` is the critical admin alert — this feeder's current fault has already exceeded what its band permits.

### Default sort order

Feeders are returned **Band A first → B → C → D → E**, then alphabetically by name within each band. No sort parameter needed.

---

## 2. Compliance Summary Block

Added to: **overview**, **all states**, **single state**, **all districts**, **single district**, **service-bands**.

Every one of these endpoints now includes a `compliance` key at the top level of the response.

### Shape

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

| Field | Description |
|---|---|
| `total_feeders` | All onboarded feeders with a band assigned |
| `compliant` | Feeders meeting their band target for the period |
| `non_compliant` | Feeders with data but below their band target |
| `no_data` | Feeders with no readings submitted — cannot be assessed |
| `by_band` | Same three counts broken down per band, ordered A → E |

> `non_compliant + no_data + compliant = total_feeders`

### Which endpoints carry this block

| Endpoint | Compliance block | Scoped to |
|---|---|---|
| `GET /api/technical/overview/` | ✅ | Whole network |
| `GET /api/technical/states/all/` | ✅ | Whole network |
| `GET /api/technical/states/single/?state=Kano` | ✅ | That state only |
| `GET /api/technical/business-districts/all/?state=Kano` | ✅ | That state |
| `GET /api/technical/business-districts/single/?district=Kano+Industrial` | ✅ | That district only |
| `GET /api/technical/service-bands/` | ✅ | Whole network |

All endpoints respect `feeder_type=11kv` or `feeder_type=33kv` — the compliance block is filtered accordingly.

---

## UI Implementation Notes

### Compliance status colours (feeder list)
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
- `has_interruption: true, breaching: false` → amber badge: *"Fault — X hrs (within allowance)"*
- `has_interruption: true, breaching: true` → red badge: *"BREACHING — X hrs (band allows Y hrs)"*

### Summary block display
Show three numbers prominently:
- **Compliant** → green
- **Non-compliant** → red
- **No data** → grey (separate from non-compliant — these are unread, not failing)

The `by_band` array drives the band-level breakdown table. Always render Band A row first.

---

*KEDCO Raven Technical Module — Compliance additions to existing endpoints.*
