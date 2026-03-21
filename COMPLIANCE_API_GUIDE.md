# KEDCO Raven — Service Level Compliance API
### Frontend Integration Guide

**Base URL (staging):** `https://staging.apiraven.raven-emrc.com/api/technical/compliance/`
**Auth:** Bearer token in `Authorization` header (same as existing Raven auth)
**All responses:** `application/json`

---

## Table of Contents
1. [What This Module Is](#1-what-this-module-is)
2. [NERC/MYTO Methodology](#2-nercmyto-methodology)
3. [Global Query Parameters](#3-global-query-parameters)
4. [Compliance Status Reference](#4-compliance-status-reference)
5. [Drill-Down Hierarchy](#5-drill-down-hierarchy)
6. [Overview](#6-overview)
7. [States](#7-states)
8. [Districts](#8-districts)
9. [Bands](#9-bands)
10. [Feeders](#10-feeders)
11. [Single Feeder Detail](#11-single-feeder-detail)
12. [Quick Reference — All Endpoints](#12-quick-reference--all-endpoints)

---

## 1. What This Module Is

This module tracks **NERC KPI compliance** — whether KEDCO is honoring the minimum hours-of-supply commitment for each feeder as mandated by the MYTO (Multi-Year Tariff Order).

Every feeder in the network is assigned to a **Service Band (A–E)**. Each band carries a daily minimum supply obligation. KEDCO is legally required to meet these targets. NERC monitors and enforces them.

This API gives operations, management, and field teams a real-time intelligence layer to:
- See at a glance how many feeders are compliant, at risk, or critical
- Drill from system-wide → state → district → band → individual feeder
- Identify feeders eligible for upgrade based on sustained performance
- Provide evidence for NERC reporting

---

## 2. NERC/MYTO Methodology

### Band Targets (hours of supply per day)

| Band | Minimum Hours/Day | Typical Customers |
|------|-------------------|-------------------|
| A    | **20 hrs/day**    | High-end commercial, premium industrial |
| B    | **16 hrs/day**    | Commercial, medium industrial |
| C    | **12 hrs/day**    | Mixed residential/commercial |
| D    | **8 hrs/day**     | Residential |
| E    | **4 hrs/day**     | Low-density residential |

### How Hours Are Counted

Hours of supply per feeder per day are derived from `HourlyLoad` data — the system checks how many distinct hours in a day had `load_mw > 0` for that feeder. A feeder that had power from 06:00–02:00 (20 hours) registers 20. A feeder with no load data registers 0.

> **Important for the UI:** A feeder showing 0 hours may mean genuinely no supply *or* no data submitted yet for that day. Use `has_data: true/false` to differentiate.

### Compliance Status Rules

Status is determined by the **most recent consecutive streak** from the end of the selected period — not just the average. This matches how NERC enforces violations.

| Status | Condition | NERC Consequence |
|--------|-----------|------------------|
| `compliant` | No consecutive failure streak ≥ 2 days | None |
| `at_risk` | **≥ 2 consecutive days** below target | KEDCO must publish public explanation |
| `critical` | **≥ 7 consecutive days** below target | NERC auto-downgrade trigger |
| `upgrade_eligible` | **≥ 7 consecutive days** at the next band's level | Feeder qualifies for band upgrade |

> **Consecutive days are measured from the last day of the selected period backward.** So for January 2026, the streak counts from January 31 backward. For the current month, it counts from today backward.

### Upgrade Eligibility Logic

A feeder can be flagged for upgrade if it **sustains the next band's target for 7 straight days**:

| Current Band | Must Hit | To Upgrade To |
|---|---|---|
| B | ≥ 20 hrs/day for 7 days | A |
| C | ≥ 16 hrs/day for 7 days | B |
| D | ≥ 12 hrs/day for 7 days | C |
| E | ≥ 8 hrs/day for 7 days | D |

Band A feeders cannot be `upgrade_eligible` — they are already the highest band.

### Performance — One Query Per Request

The entire compliance calculation for all feeders (regardless of count) runs in **one SQL query**. Python groups the results by state/district/band. There is no N+1 problem.

---

## 3. Global Query Parameters

These work on **every** compliance endpoint.

| Parameter | Values | Default | Description |
|---|---|---|---|
| `mode` | `daily` `weekly` `monthly` `yearly` | `monthly` | Time window |
| `year` | e.g. `2026` | current year | Used with `monthly` / `yearly` |
| `month` | `1`–`12` | current month | Used with `monthly` |
| `from_date` | `YYYY-MM-DD` | — | Used with `daily` / `weekly` |
| `to_date` | `YYYY-MM-DD` | — | Used with `weekly` / `custom` |
| `voltage_level` | `11kv` `33kv` | *(all)* | Filter by feeder voltage |
| `state` | State slug e.g. `KN` | *(all)* | Scope to a state |
| `district` | District name e.g. `KN-IDU` | *(all)* | Scope to a district |

> **Always pass an explicit period.** Defaulting to the current month will show partial data if the month is in progress.

### Examples
```
?mode=monthly&year=2026&month=1
?mode=monthly&year=2026&month=1&voltage_level=11kv
?mode=monthly&year=2026&month=1&state=KN
?mode=daily&from_date=2026-03-15
?mode=yearly&year=2026
```

---

## 4. Compliance Status Reference

Every feeder and every summary in this API carries a `status` field. Use this for colour-coding in the UI.

| Status | Meaning | Suggested UI Treatment |
|--------|---------|----------------------|
| `compliant` | Meeting NERC target | Green |
| `at_risk` | 2+ consecutive days failing — must publish | Amber / warning badge |
| `critical` | 7+ consecutive days failing — NERC downgrade trigger | Red / alert badge |
| `upgrade_eligible` | 7+ consecutive days at next band's level | Blue / upgrade badge |

### Summary Object Shape

Every level (overview, state, district, band) returns the same summary block:

```json
{
  "total_feeders": 100,
  "compliant": 70,
  "at_risk": 20,
  "critical": 5,
  "upgrade_eligible": 5
}
```

### by_band Array Shape

Every level (overview, state, district) also returns a `by_band` array, **always ordered A → E**:

```json
[
  {
    "band": { "slug": "a", "name": "A", "target_hours_per_day": 20.0 },
    "total": 30,
    "compliant": 20,
    "at_risk": 8,
    "critical": 2,
    "upgrade_eligible": 0,
    "avg_compliance_pct": 74.5
  },
  { "band": { "slug": "b", ... }, ... },
  { "band": { "slug": "c", ... }, ... },
  { "band": { "slug": "d", ... }, ... },
  { "band": { "slug": "e", ... }, ... }
]
```

> **UI Tip:** Band A row is always most important for KEDCO operations. Highlight it prominently.

### Per-Feeder Compliance Object Shape

```json
{
  "status": "at_risk",
  "avg_hours_supplied": 18.4,
  "target_hours_per_day": 20.0,
  "compliance_pct": 92.0,
  "days_in_period": 31,
  "days_compliant": 28,
  "days_failed": 3,
  "consecutive_days_failed": 2,
  "consecutive_days_at_next_level": 0,
  "upgrade_eligible": false,
  "has_data": true
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | `compliant` `at_risk` `critical` `upgrade_eligible` |
| `avg_hours_supplied` | float | Average hrs/day over the period |
| `target_hours_per_day` | float | Band's NERC minimum |
| `compliance_pct` | float | `avg_hours / target × 100` |
| `days_in_period` | int | Total days in selected period |
| `days_compliant` | int | Days the feeder met or exceeded target |
| `days_failed` | int | Days the feeder fell below target |
| `consecutive_days_failed` | int | **Key NERC metric** — current streak of failures from end of period |
| `consecutive_days_at_next_level` | int | Current streak of days at the next band's level |
| `upgrade_eligible` | bool | `true` if `consecutive_days_at_next_level >= 7` |
| `has_data` | bool | `false` means no HourlyLoad data — distinguish from genuine 0-hr supply |

---

## 5. Drill-Down Hierarchy

```
/compliance/overview/           ← System-wide: total + by_band
      │
      ├── /compliance/states/           ← All states, each: total + by_band
      │         │
      │         └── /compliance/states/<slug>/    ← Single state: total + by_band
      │                   │
      │                   └── /compliance/districts/?state=<slug>
      │
      ├── /compliance/districts/        ← All districts, each: total + by_band
      │         │
      │         └── /compliance/districts/<slug>/  ← Single district: total + by_band
      │                   │
      │                   └── /compliance/bands/?district=<slug>
      │
      ├── /compliance/bands/            ← All bands A→E, each: summary
      │         │
      │         └── /compliance/bands/<slug>/      ← Single band: summary + feeder list
      │                   │
      │                   └── /compliance/feeders/<slug>/
      │
      └── /compliance/feeders/          ← All feeders ordered A→E, per-feeder compliance
                │
                └── /compliance/feeders/<slug>/    ← Single feeder: summary + day-by-day
```

**Typical user journey:**
1. Land on overview → see system-wide numbers + by_band breakdown
2. Click a state → scoped state view
3. Click a district → scoped district view
4. Click a band row (e.g. Band A) → band detail with all feeders in that band/district
5. Click a feeder → day-by-day compliance calendar

---

## 6. Overview

> System-wide compliance. The dashboard entry point.

```
GET /api/technical/compliance/overview/
GET /api/technical/compliance/overview/?mode=monthly&year=2026&month=1
GET /api/technical/compliance/overview/?state=KN&voltage_level=11kv
```

### Response
```json
{
  "period": {
    "mode": "monthly",
    "start_date": "2026-01-01",
    "end_date": "2026-01-31",
    "days": 31
  },
  "filters": {
    "voltage_level": "all",
    "state": null,
    "district": null
  },
  "summary": {
    "total_feeders": 100,
    "compliant": 70,
    "at_risk": 20,
    "critical": 5,
    "upgrade_eligible": 5
  },
  "by_band": [
    {
      "band": { "slug": "a", "name": "A", "target_hours_per_day": 20.0 },
      "total": 30,
      "compliant": 20,
      "at_risk": 8,
      "critical": 2,
      "upgrade_eligible": 0,
      "avg_compliance_pct": 74.5
    },
    {
      "band": { "slug": "b", "name": "B", "target_hours_per_day": 16.0 },
      "total": 25,
      "compliant": 18,
      "at_risk": 5,
      "critical": 1,
      "upgrade_eligible": 2,
      "avg_compliance_pct": 88.2
    },
    { "band": { "slug": "c", ... }, ... },
    { "band": { "slug": "d", ... }, ... },
    { "band": { "slug": "e", ... }, ... }
  ]
}
```

---

## 7. States

### List all states
```
GET /api/technical/compliance/states/
GET /api/technical/compliance/states/?voltage_level=11kv
```

### Single state
```
GET /api/technical/compliance/states/<slug>/
GET /api/technical/compliance/states/KN/?mode=monthly&year=2026&month=1
```

**Slug examples:** `KN` `JG` `KS`

### Response (list)
```json
{
  "period": { ... },
  "filters": { "voltage_level": "all" },
  "count": 3,
  "states": [
    {
      "state": { "slug": "KN", "name": "Kano" },
      "summary": {
        "total_feeders": 45,
        "compliant": 30,
        "at_risk": 10,
        "critical": 3,
        "upgrade_eligible": 2
      },
      "by_band": [ ... ]
    },
    ...
  ]
}
```

### Response (single state)
Same shape as one item above, with `period` and `filters` at the top level.

```json
{
  "period": { ... },
  "filters": { "voltage_level": "all" },
  "state": { "slug": "KN", "name": "Kano" },
  "summary": { ... },
  "by_band": [ ... ]
}
```

---

## 8. Districts

### List all districts
```
GET /api/technical/compliance/districts/
GET /api/technical/compliance/districts/?state=KN
GET /api/technical/compliance/districts/?state=KN&voltage_level=33kv
```

### Single district
```
GET /api/technical/compliance/districts/<slug>/
GET /api/technical/compliance/districts/KN-IDU/?mode=monthly&year=2026&month=1
```

**Slug examples:** `KN-IDU` `KN-NW` `JG-DUT`

### Response (list)
```json
{
  "period": { ... },
  "filters": { "voltage_level": "all", "state": "KN" },
  "count": 6,
  "districts": [
    {
      "district": {
        "slug": "KN-IDU",
        "name": "Kano Industrial",
        "state": { "slug": "KN", "name": "Kano" }
      },
      "summary": {
        "total_feeders": 12,
        "compliant": 8,
        "at_risk": 3,
        "critical": 1,
        "upgrade_eligible": 0
      },
      "by_band": [ ... ]
    },
    ...
  ]
}
```

### Response (single district)
```json
{
  "period": { ... },
  "filters": { "voltage_level": "all" },
  "district": {
    "slug": "KN-IDU",
    "name": "Kano Industrial",
    "state": { "slug": "KN", "name": "Kano" }
  },
  "summary": { ... },
  "by_band": [ ... ]
}
```

---

## 9. Bands

### List all bands
```
GET /api/technical/compliance/bands/
GET /api/technical/compliance/bands/?state=KN
GET /api/technical/compliance/bands/?district=KN-IDU&voltage_level=11kv
```

### Single band
```
GET /api/technical/compliance/bands/<slug>/
GET /api/technical/compliance/bands/a/?state=KN
GET /api/technical/compliance/bands/a/?district=KN-IDU&voltage_level=11kv
```

**Slugs:** `a` `b` `c` `d` `e`

### Response (list) — always ordered A → E
```json
{
  "period": { ... },
  "filters": { "voltage_level": "all", "state": null, "district": null },
  "count": 5,
  "bands": [
    {
      "band": {
        "slug": "a",
        "name": "A",
        "target_hours_per_day": 20.0,
        "upgrade_target_hours": null
      },
      "summary": {
        "total_feeders": 30,
        "compliant": 20,
        "at_risk": 8,
        "critical": 2,
        "upgrade_eligible": 0
      }
    },
    {
      "band": {
        "slug": "b",
        "name": "B",
        "target_hours_per_day": 16.0,
        "upgrade_target_hours": 20.0
      },
      "summary": { ... }
    },
    ...
  ]
}
```

> `upgrade_target_hours` — what this band's feeders must sustain for 7 days to qualify for upgrade. `null` for Band A (already highest).

### Response (single band) — summary + every feeder in that band

```json
{
  "period": { ... },
  "filters": { "voltage_level": "all", "state": null, "district": null },
  "band": {
    "slug": "a",
    "name": "A",
    "target_hours_per_day": 20.0,
    "upgrade_target_hours": null
  },
  "summary": {
    "total_feeders": 30,
    "compliant": 20,
    "at_risk": 8,
    "critical": 2,
    "upgrade_eligible": 0
  },
  "feeders": [
    {
      "feeder": {
        "slug": "kn-tam-coc",
        "name": "COCA COLA",
        "voltage_level": "11kv",
        "district": { "slug": "KN-IDU", "name": "Kano Industrial" },
        "state": { "slug": "KN", "name": "Kano" }
      },
      "compliance": {
        "status": "at_risk",
        "avg_hours_supplied": 18.4,
        "target_hours_per_day": 20.0,
        "compliance_pct": 92.0,
        "days_in_period": 31,
        "days_compliant": 28,
        "days_failed": 3,
        "consecutive_days_failed": 2,
        "consecutive_days_at_next_level": 0,
        "upgrade_eligible": false,
        "has_data": true
      }
    },
    ...
  ]
}
```

> Feeders are ordered **alphabetically by name** within the band (all same band so no band-ordering needed here).

---

## 10. Feeders

### List all feeders
```
GET /api/technical/compliance/feeders/
GET /api/technical/compliance/feeders/?band=a
GET /api/technical/compliance/feeders/?band=a&state=KN
GET /api/technical/compliance/feeders/?band=a&state=KN&voltage_level=11kv
GET /api/technical/compliance/feeders/?voltage_level=33kv
```

| Filter | Description |
|---|---|
| `band` | `a` `b` `c` `d` `e` — filter to one band |
| `voltage_level` | `11kv` or `33kv` |
| `state` | State slug |
| `district` | District slug |

> **Default ordering: Band A first, then B, C, D, E.** Within each band: alphabetical by feeder name.

### Response
```json
{
  "period": { ... },
  "filters": {
    "band": "all",
    "voltage_level": "all",
    "state": null,
    "district": null
  },
  "count": 100,
  "feeders": [
    {
      "feeder": {
        "slug": "kn-tam-coc",
        "name": "COCA COLA",
        "voltage_level": "11kv",
        "district": { "slug": "KN-IDU", "name": "Kano Industrial" },
        "state": { "slug": "KN", "name": "Kano" }
      },
      "band": {
        "slug": "a",
        "name": "A",
        "target_hours_per_day": 20.0
      },
      "compliance": {
        "status": "critical",
        "avg_hours_supplied": 11.2,
        "target_hours_per_day": 20.0,
        "compliance_pct": 56.0,
        "days_in_period": 31,
        "days_compliant": 10,
        "days_failed": 21,
        "consecutive_days_failed": 9,
        "consecutive_days_at_next_level": 0,
        "upgrade_eligible": false,
        "has_data": true
      }
    },
    ...
  ]
}
```

---

## 11. Single Feeder Detail

> Day-by-day breakdown for a specific feeder. Use this for the compliance calendar / chart view.

```
GET /api/technical/compliance/feeders/<slug>/
GET /api/technical/compliance/feeders/kn-tam-coc/?mode=monthly&year=2026&month=1
```

### Response
```json
{
  "period": {
    "mode": "monthly",
    "start_date": "2026-01-01",
    "end_date": "2026-01-31",
    "days": 31
  },
  "feeder": {
    "slug": "kn-tam-coc",
    "name": "COCA COLA",
    "voltage_level": "11kv",
    "district": { "slug": "KN-IDU", "name": "Kano Industrial" },
    "state": { "slug": "KN", "name": "Kano" }
  },
  "band": {
    "slug": "a",
    "name": "A",
    "target_hours_per_day": 20.0
  },
  "compliance": {
    "status": "at_risk",
    "avg_hours_supplied": 18.4,
    "target_hours_per_day": 20.0,
    "compliance_pct": 92.0,
    "days_in_period": 31,
    "days_compliant": 28,
    "days_failed": 3,
    "consecutive_days_failed": 2,
    "consecutive_days_at_next_level": 0,
    "upgrade_eligible": false,
    "has_data": true
  },
  "daily": [
    {
      "date": "2026-01-01",
      "hours_supplied": 22.0,
      "target_hours": 20.0,
      "compliant": true,
      "at_next_level": false
    },
    {
      "date": "2026-01-02",
      "hours_supplied": 19.0,
      "target_hours": 20.0,
      "compliant": false,
      "at_next_level": false
    },
    ...
    {
      "date": "2026-01-31",
      "hours_supplied": 17.5,
      "target_hours": 20.0,
      "compliant": false,
      "at_next_level": false
    }
  ]
}
```

| Daily Field | Description |
|---|---|
| `date` | ISO date string |
| `hours_supplied` | Actual hours with `load_mw > 0` that day (0–24) |
| `target_hours` | Band's NERC minimum for that feeder |
| `compliant` | `true` if `hours_supplied >= target_hours` |
| `at_next_level` | `true` if this day counts toward upgrade eligibility |

> **UI Tip:** Use `daily` to render a compliance calendar (green = compliant, red = failed). The last N consecutive red days = `consecutive_days_failed`. Show a progress bar toward the 7-day upgrade streak using `consecutive_days_at_next_level`.

---

## 12. Quick Reference — All Endpoints

```
GET /api/technical/compliance/overview/                            System-wide summary + by_band
GET /api/technical/compliance/overview/?state=KN                   Scoped to Kano
GET /api/technical/compliance/overview/?voltage_level=11kv         11kV feeders only

GET /api/technical/compliance/states/                              All states: summary + by_band each
GET /api/technical/compliance/states/?voltage_level=33kv           33kV feeders only
GET /api/technical/compliance/states/KN/                           Single state detail

GET /api/technical/compliance/districts/                           All districts
GET /api/technical/compliance/districts/?state=KN                  Districts in Kano
GET /api/technical/compliance/districts/KN-IDU/                    Single district detail

GET /api/technical/compliance/bands/                               All bands A→E with summary
GET /api/technical/compliance/bands/?state=KN                      Scoped to Kano
GET /api/technical/compliance/bands/?district=KN-IDU               Scoped to district
GET /api/technical/compliance/bands/a/                             Band A: summary + feeder list
GET /api/technical/compliance/bands/a/?state=KN                    Band A feeders in Kano
GET /api/technical/compliance/bands/a/?district=KN-IDU&voltage_level=11kv

GET /api/technical/compliance/feeders/                             All feeders ordered A→E
GET /api/technical/compliance/feeders/?band=a                      Only Band A feeders
GET /api/technical/compliance/feeders/?band=a&state=KN
GET /api/technical/compliance/feeders/?voltage_level=11kv
GET /api/technical/compliance/feeders/kn-tam-coc/                  Single feeder + day-by-day
```

---

## UI Implementation Notes

### Status colour mapping
```js
const STATUS_COLORS = {
  compliant:        '#22c55e',   // green
  at_risk:          '#f59e0b',   // amber
  critical:         '#ef4444',   // red
  upgrade_eligible: '#3b82f6',   // blue
}
```

### Compliance calendar (single feeder)
Render each item in `daily[]` as a cell. Colour: green if `compliant`, red if not. Grey out cells where `hours_supplied === 0 && !has_data` (no data submitted).

### Band A priority
Always render the Band A row first and with extra visual weight — it carries the highest NERC obligation and is KEDCO's primary regulatory exposure.

### Consecutive days display
- `consecutive_days_failed >= 7` → show red alert: *"NERC downgrade risk — 7+ days"*
- `consecutive_days_failed >= 2` → show amber warning: *"At risk — must publish explanation"*
- `consecutive_days_at_next_level` → show upgrade progress bar: *"X / 7 days toward Band A"*

### has_data flag
If `has_data: false`, show the metric as `—` or `No data` rather than `0 hrs`. Zero hours and no data are operationally different — zero means the feeder had no supply, no data means the field team hasn't submitted readings yet.

### Voltage level filter
Use `voltage_level=11kv` or `voltage_level=33kv` consistently across all views. The filter parameter is the same on every endpoint.

---

*KEDCO Raven Service Level Compliance Module — NERC KPI Tracking — staging environment.*
