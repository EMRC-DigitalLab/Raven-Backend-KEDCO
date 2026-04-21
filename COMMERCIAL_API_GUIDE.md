# KEDCO Raven — Commercial Analytics API
### Frontend Integration Guide

**Base URL (staging):** `https://staging.apiraven.raven-emrc.com/api/commercial/`
**Auth:** Bearer token in `Authorization` header
**All responses:** `application/json`

---

## Table of Contents
1. [What This Module Is](#1-what-this-module-is)
2. [Global Query Parameters](#2-global-query-parameters)
3. [Metric Object Shape](#3-metric-object-shape)
4. [Data Modes](#4-data-modes)
5. [Energy Consumed vs Energy Delivered](#5-energy-consumed-vs-energy-delivered)
6. [AT&C Loss Formula](#6-atc-loss-formula)
7. [Overview](#7-overview)
8. [States](#8-states)
9. [Districts](#9-districts)
10. [Feeders](#10-feeders)
11. [Service Bands](#11-service-bands)
12. [Customers](#12-customers)
13. [Trend — Last 4 Periods](#13-trend--last-4-periods)
14. [KPI Reference](#14-kpi-reference)
15. [Quick Reference — All Endpoints](#15-quick-reference--all-endpoints)

---

## 1. What This Module Is

This module tracks **commercial performance** for KEDCO's MDI and MDNI customers — billing, revenue, energy consumption, energy delivered, coverage, and AT&C losses.

> **MDI** = Maximum Demand Industrial
> **MDNI** = Non Maximum Demand Industrial (smaller commercial customers)

Every endpoint supports the same time period filters and returns the same metric structure, so the frontend can reuse components across all levels.

---

## 2. Global Query Parameters

These work on **every** endpoint.

| Parameter | Values | Default | Description |
|---|---|---|---|
| `mode` | `daily` `weekly` `monthly` `yearly` | `monthly` | Time window |
| `year` | e.g. `2026` | current year | Used with `monthly` / `yearly` |
| `month` | `1`–`12` | current month | Used with `monthly` |
| `from_date` | `YYYY-MM-DD` | today | Used with `daily` / `weekly` |
| `type` | `MDI` `MDNI` | *(all)* | Filter to one customer segment |

> **No `feeder_type` filter on the commercial module.** Voltage level (11kV / 33kV) is a technical module concept. Commercial customers are scoped by `type=MDI` or `type=MDNI` only.

### Examples
```
?mode=monthly&year=2026&month=1
?mode=monthly&year=2026&month=1&type=MDI
?mode=daily&from_date=2026-03-15
?mode=yearly&year=2026
?mode=weekly&from_date=2026-03-10
```

> **Always pass an explicit period.** Defaulting to the current month returns 0 for all metrics if readings haven't been submitted yet this month.

---

## 3. Metric Object Shape

**Every KPI** is returned in this wrapper — consistent across all endpoints and all levels.

```json
{
  "value": 35069892.61,
  "unit": "kWh",
  "mode": "actual",
  "explanation": "Total energy consumed = sum(present_reading - previous_reading) for all customers read in this period."
}
```

| Field | Type | Description |
|---|---|---|
| `value` | `number` or `object` | The KPI value |
| `unit` | `string` | `""` `"kWh"` `"kWh/day"` `"MWh/day"` `"NGN"` `"%"` |
| `mode` | `string` | See [Data Modes](#4-data-modes) |
| `explanation` | `string` | Use as tooltip on every KPI card — always present |

---

## 4. Data Modes

| Mode | Meaning | Suggested UI Treatment |
|---|---|---|
| `"actual"` | From real meter readings | Solid, confirmed — no badge needed |
| `"estimated"` | Projected for unread customers (last known daily avg × days) | ≈ prefix, dashed border, or italic |
| `"meter"` | Energy delivered — sourced from injection substation meter records | Green badge: "Metered" |
| `"system"` | Energy delivered — estimated from HourlyLoad (no meter data) | Amber badge: "Estimated" |
| `"mixed"` | Energy delivered — some feeders metered, some estimated | Blue badge: "Mixed" |

---

## 5. Energy Consumed vs Energy Delivered

These are two distinct metrics. Both appear in **every** view at **every** level.

### `energy_consumed_kwh` — Raw Meter Consumption
```
energy_consumed_kwh = SUM(present_reading - previous_reading)
```
This is the actual delta on the customer's meter register — what the meter physically recorded. It is **not** the same as billed consumption.

**Present in:**
- Overview → `energy.energy_consumed_kwh`
- All states / Single state → `energy.energy_consumed_kwh`
- All districts / Single district → `energy.energy_consumed_kwh`
- All feeders / Single feeder → `energy.energy_consumed_kwh`
- Trend → `energy_consumed_kwh` per period

### `energy_delivered_kwh` — Grid Input
Energy injected into the feeder from the grid (from the technical module).
- **PRIMARY source:** `EnergyDelivered` table — actual injection meter sum for the period
- **FALLBACK:** `HourlyLoad` avg × supply hours (when meter data is missing)
- `mode` tells you which source: `"meter"` / `"system"` / `"mixed"`

### `actual_billed_kwh` — What Was Billed
`billed_consumption` from meter readings — what KEDCO actually charged customers.

### Why These Three Are Different
| Metric | What It Measures |
|---|---|
| `energy_delivered_kwh` | Energy sent from grid into the feeder |
| `energy_consumed_kwh` | Energy the meters physically recorded |
| `actual_billed_kwh` | Energy KEDCO formally billed on the invoice |

The gap between delivered and billed drives the AT&C loss calculation.

---

## 6. AT&C Loss Formula

```
AT&C Loss (%) = (1 - energy_billed_kwh / energy_delivered_kwh) × 100
```

Equivalently:
```
billing_efficiency (%) = energy_billed_kwh / energy_delivered_kwh × 100
AT&C Loss (%)          = 100 - billing_efficiency
```

> **If AT&C loss is negative** — this means `energy_billed > energy_delivered`. This happens when the technical `EnergyDelivered` data is incomplete for commercial feeders (a data gap, not a code error). **Display as `N/A`** when the value is negative.

> **If `energy_delivered_kwh` is 0** — the technical module has no data for those feeders. Display both `billing_efficiency` and `atc_loss` as `N/A`.

---

## 7. Overview

> System-wide commercial metrics. The main dashboard entry point.

```
GET /api/commercial/overview/
GET /api/commercial/overview/?mode=monthly&year=2026&month=1
GET /api/commercial/overview/?mode=monthly&year=2026&month=1&type=MDI
```

### Response
```json
{
  "period": {
    "mode":       "monthly",
    "start_date": "2026-01-01",
    "end_date":   "2026-01-31",
    "label":      "January 2026",
    "days":       31
  },

  "total_feeders": 50,

  "customers": {
    "total":        { "value": 1871, "unit": "", "mode": "actual", "explanation": "Total registered MDI and MDNI customers." },
    "mdi":          { "value": 1157, "unit": "", "mode": "actual", "explanation": "Customers classified as Maximum Demand Installation (MDI)." },
    "mdni":         { "value": 714,  "unit": "", "mode": "actual", "explanation": "Customers classified as Non Maximum Demand (MDNI)." },
    "bypass_count": { "value": 3,    "unit": "", "mode": "actual", "explanation": "Customers flagged for meter bypass / tampering." }
  },

  "energy": {
    "energy_consumed_kwh":        { "value": 35069892.61, "unit": "kWh",     "mode": "actual",    "explanation": "Sum of (present_reading - previous_reading) for all customers read." },
    "actual_billed_kwh":          { "value": 35069892.61, "unit": "kWh",     "mode": "actual",    "explanation": "Energy billed from real meter readings." },
    "estimated_billed_kwh":       { "value": 0.0,         "unit": "kWh",     "mode": "estimated", "explanation": "Projected for unread customers." },
    "total_projected_billed_kwh": { "value": 35069892.61, "unit": "kWh",     "mode": "estimated", "explanation": "Actual + estimated." },
    "daily_billed_kwh_estimate":  { "value": 1131931.0,   "unit": "kWh/day", "mode": "estimated", "explanation": "Actual billed ÷ days." },
    "daily_energy_delivered_mwh": { "value": 502.5,       "unit": "MWh/day", "mode": "mixed",     "explanation": "Total delivered ÷ days." },
    "energy_delivered_kwh":       { "value": 15578100.0,  "unit": "kWh",     "mode": "mixed",     "explanation": "Total energy injected into feeders this period." },
    "energy_delivered_vs_billed": {
      "value": {
        "delivered_kwh":        15578100.0,
        "actual_billed_kwh":    35069892.61,
        "projected_billed_kwh": 35069892.61,
        "gap_kwh":              -19491792.61
      },
      "unit": "kWh", "mode": "mixed", "explanation": "..."
    }
  },

  "revenue": {
    "actual_energy_charge":    { "value": 7339099640.67, "unit": "NGN", "mode": "actual",    "explanation": "..." },
    "estimated_energy_charge": { "value": 0.0,           "unit": "NGN", "mode": "estimated", "explanation": "..." },
    "actual_vat":              { "value": 550432473.05,  "unit": "NGN", "mode": "actual",    "explanation": "..." },
    "actual_total_billed":     { "value": 7889532113.72, "unit": "NGN", "mode": "actual",    "explanation": "..." },
    "estimated_revenue":       { "value": 0.0,           "unit": "NGN", "mode": "estimated", "explanation": "..." },
    "total_projected_revenue": { "value": 7889532113.72, "unit": "NGN", "mode": "estimated", "explanation": "..." },
    "mdi_revenue_split":       { "value": 100.0,         "unit": "%",   "mode": "actual",    "explanation": "..." },
    "mdni_revenue_split":      { "value": 0.0,           "unit": "%",   "mode": "actual",    "explanation": "..." },
    "arpu":                    { "value": 6813668.5,     "unit": "NGN", "mode": "actual",    "explanation": "..." }
  },

  "performance": {
    "coverage_rate":      { "value": 99.46, "unit": "%", "mode": "actual",    "explanation": "..." },
    "customers_read":     { "value": 1151,  "unit": "",  "mode": "actual",    "explanation": "..." },
    "unread_customers":   { "value": 6,     "unit": "",  "mode": "actual",    "explanation": "..." },
    "billing_efficiency": { "value": 225.1, "unit": "%", "mode": "estimated", "explanation": "energy_billed / energy_delivered × 100. Show N/A if > 100% or negative." },
    "atc_loss":           { "value": -125.1,"unit": "%", "mode": "estimated", "explanation": "(1 - billed/delivered) × 100. Show N/A if negative." }
  },

  "managers": {
    "total_mdi_managers":  { "value": 60, "unit": "", "mode": "actual", "explanation": "..." },
    "total_mdni_managers": { "value": 0,  "unit": "", "mode": "actual", "explanation": "..." }
  },

  "energy_breakdown": {
    "by_state": [
      {
        "state": { "slug": "JG", "name": "Jigawa" },
        "energy_delivered_kwh": 0.0,
        "energy_consumed_kwh":  580234.5,
        "actual_billed_kwh":    579000.0,
        "atc_loss":             null,
        "mode":                 "system"
      },
      {
        "state": { "slug": "KN", "name": "Kano" },
        "energy_delivered_kwh": 7778810.0,
        "energy_consumed_kwh":  33490517.61,
        "actual_billed_kwh":    33552126.86,
        "atc_loss":             -331.4,
        "mode":                 "mixed"
      },
      {
        "state": { "slug": "KS", "name": "Katsina" },
        "energy_delivered_kwh": 7799290.0,
        "energy_consumed_kwh":  938766.0,
        "actual_billed_kwh":    938766.0,
        "atc_loss":             -88.0,
        "mode":                 "mixed"
      }
    ],
    "by_district": [
      {
        "district": { "slug": "KN-IDU", "name": "Kano Industrial", "state": "KN" },
        "energy_delivered_kwh": 3200000.0,
        "energy_consumed_kwh":  12500000.0,
        "actual_billed_kwh":    12480000.0,
        "atc_loss":             -290.0,
        "mode":                 "meter"
      },
      { "district": { "slug": "KN-NW", ... }, ... },
      ...
    ],
    "by_band": [
      {
        "band": { "slug": "a", "name": "A" },
        "energy_delivered_kwh": 8200000.0,
        "energy_consumed_kwh":  20100000.0,
        "actual_billed_kwh":    20050000.0,
        "atc_loss":             -144.5,
        "mode":                 "mixed"
      },
      { "band": { "slug": "b", "name": "B" }, ... },
      ...
    ]
  }
}
```

### `total_feeders`
Count of feeders that have at least one commercial customer. Respects the `type=MDI/MDNI` filter.

### `energy_breakdown`
Compact energy summary at every dimension — no need to call the states/districts/bands endpoints separately just to get energy figures for a dashboard chart.

| Field | Description |
|---|---|
| `energy_delivered_kwh` | Energy from grid for this group in this period |
| `energy_consumed_kwh` | Sum of (present − previous) meter registers |
| `actual_billed_kwh` | Energy formally billed |
| `atc_loss` | `(1 − billed/delivered) × 100` — **show as N/A if null or negative** |
| `mode` | `meter` / `system` / `mixed` — energy delivered source |

---

## 8. States

### List all states
```
GET /api/commercial/states/
GET /api/commercial/states/?mode=monthly&year=2026&month=1
GET /api/commercial/states/?mode=monthly&year=2026&month=1&type=MDI
```

### Single state
```
GET /api/commercial/states/<slug>/
GET /api/commercial/states/KN/?mode=monthly&year=2026&month=1
```
**Slug examples:** `KN` `JG` `KS`

### Response — list
```json
{
  "period": { "mode": "monthly", "start_date": "2026-01-01", "end_date": "2026-01-31", "label": "January 2026", "days": 31 },
  "count": 3,
  "states": [
    {
      "state": { "slug": "KN", "name": "Kano" },
      "customers":   { "total": {...}, "mdi": {...}, "mdni": {...}, "bypass_count": {...} },
      "energy": {
        "energy_consumed_kwh":        { "value": 33490517.61, "unit": "kWh", "mode": "actual",    "explanation": "..." },
        "actual_billed_kwh":          { "value": 33552126.86, "unit": "kWh", "mode": "actual",    "explanation": "..." },
        "estimated_billed_kwh":       { "value": 0.0,         "unit": "kWh", "mode": "estimated", "explanation": "..." },
        "total_projected_billed_kwh": { "value": 33552126.86, "unit": "kWh", "mode": "estimated", "explanation": "..." },
        "daily_billed_kwh_estimate":  { "value": 1082326.67,  "unit": "kWh/day", "mode": "estimated", "explanation": "..." },
        "daily_energy_delivered_mwh": { "value": 250.93,      "unit": "MWh/day", "mode": "mixed",  "explanation": "..." },
        "energy_delivered_kwh":       { "value": 7778810.0,   "unit": "kWh", "mode": "mixed",    "explanation": "..." },
        "energy_delivered_vs_billed": { "value": { "delivered_kwh": 7778810.0, "actual_billed_kwh": 33552126.86, "projected_billed_kwh": 33552126.86, "gap_kwh": -25773316.86 }, "unit": "kWh", "mode": "mixed", "explanation": "..." }
      },
      "revenue":     { "actual_energy_charge": {...}, "actual_vat": {...}, "actual_total_billed": {...}, "estimated_revenue": {...}, "total_projected_revenue": {...}, "mdi_revenue_split": {...}, "mdni_revenue_split": {...}, "arpu": {...} },
      "performance": { "coverage_rate": {...}, "customers_read": {...}, "unread_customers": {...}, "billing_efficiency": {...}, "atc_loss": {...} },
      "managers":    { "total_mdi_managers": {...}, "total_mdni_managers": {...} }
    },
    ...
  ]
}
```

### Response — single state
Same shape as one item above, **plus** `period` at the top level **and** an `energy_breakdown.by_district` section:

```json
{
  "period": { ... },
  "state":       { "slug": "KN", "name": "Kano" },
  "customers":   { ... },
  "energy":      { ... },
  "revenue":     { ... },
  "performance": { ... },
  "managers":    { ... },

  "energy_breakdown": {
    "by_district": [
      {
        "district": { "slug": "KN-IDU", "name": "Kano Industrial" },
        "energy_delivered_kwh": 3200000.0,
        "energy_consumed_kwh":  12500000.0,
        "actual_billed_kwh":    12480000.0,
        "atc_loss":             -290.0,
        "mode":                 "meter"
      },
      {
        "district": { "slug": "KN-NW", "name": "Kano North West" },
        "energy_delivered_kwh": 4578810.0,
        "energy_consumed_kwh":  20990517.0,
        "actual_billed_kwh":    21072126.0,
        "atc_loss":             -360.2,
        "mode":                 "mixed"
      },
      ...
    ]
  }
}
```

> `energy_breakdown.by_district` gives the frontend everything needed to render a district-level bar chart inside a state view — without a separate API call.

---

## 9. Districts

### List all districts
```
GET /api/commercial/districts/
GET /api/commercial/districts/?state=KN
GET /api/commercial/districts/?mode=monthly&year=2026&month=1
```

### Single district
```
GET /api/commercial/districts/<slug>/
GET /api/commercial/districts/KN-IDU/?mode=monthly&year=2026&month=1
```
**Slug examples:** `KN-IDU` `KN-NW` `JG-DUT`

### Response — list
```json
{
  "period": { ... },
  "count": 17,
  "districts": [
    {
      "district": { "slug": "KN-IDU", "name": "Kano Industrial", "state": "Kano" },
      "customers":   { "total": {...}, "mdi": {...}, "mdni": {...}, "bypass_count": {...} },
      "energy":      { "energy_consumed_kwh": {...}, "actual_billed_kwh": {...}, "energy_delivered_kwh": {...}, ... },
      "revenue":     { ... },
      "performance": { "coverage_rate": {...}, "billing_efficiency": {...}, "atc_loss": {...}, ... },
      "managers":    { ... }
    },
    ...
  ]
}
```

### Response — single district
Same shape as one item above, **plus** `period` at the top level **and** an `energy_breakdown.by_feeder` section:

```json
{
  "period": { ... },
  "district": { "slug": "KN-IDU", "name": "Kano Industrial", "state": "Kano" },
  "customers":   { ... },
  "energy":      { ... },
  "revenue":     { ... },
  "performance": { ... },
  "managers":    { ... },

  "energy_breakdown": {
    "by_feeder": [
      {
        "feeder": { "slug": "kn-tam-coc", "name": "COCA COLA" },
        "energy_delivered_kwh": 800000.0,
        "energy_consumed_kwh":  3100000.0,
        "actual_billed_kwh":    3095000.0,
        "atc_loss":             -286.9,
        "mode":                 "meter"
      },
      {
        "feeder": { "slug": "kn-idu-dan", "name": "DANGOTE CEMENT" },
        "energy_delivered_kwh": 2400000.0,
        "energy_consumed_kwh":  9400000.0,
        "actual_billed_kwh":    9385000.0,
        "atc_loss":             -291.0,
        "mode":                 "meter"
      },
      ...
    ]
  }
}
```

> `energy_breakdown.by_feeder` gives the frontend a feeder-level energy breakdown inside a district view — without calling the feeders endpoint.

---

## 10. Feeders

> **Only feeders with commercial customers are returned.** This is not the full list of feeders in the database — only the ones that have at least one MDI or MDNI customer.

### List feeders with commercial customers
```
GET /api/commercial/feeders/
GET /api/commercial/feeders/?state=KN
GET /api/commercial/feeders/?district=KN-IDU
GET /api/commercial/feeders/?state=KN&type=MDI
GET /api/commercial/feeders/?mode=monthly&year=2026&month=1
```

### Single feeder
```
GET /api/commercial/feeders/<slug>/
GET /api/commercial/feeders/kn-tam-coc/?mode=monthly&year=2026&month=1
```

### Response — feeder list
```json
{
  "period": { ... },
  "count": 50,
  "feeders": [
    {
      "feeder": {
        "slug":          "kn-tam-coc",
        "name":          "COCA COLA",
        "voltage_level": "11kv",
        "feeder_class":  "MDI",
        "district": { "slug": "KN-IDU", "name": "Kano Industrial" },
        "state":    { "slug": "KN",     "name": "Kano" }
      },
      "customers":   { "total": {...}, "mdi": {...}, "mdni": {...}, "bypass_count": {...} },
      "energy": {
        "energy_consumed_kwh":        { "value": 3100000.0, "unit": "kWh", "mode": "actual",    "explanation": "..." },
        "actual_billed_kwh":          { "value": 3095000.0, "unit": "kWh", "mode": "actual",    "explanation": "..." },
        "estimated_billed_kwh":       { "value": 0.0,       "unit": "kWh", "mode": "estimated", "explanation": "..." },
        "total_projected_billed_kwh": { "value": 3095000.0, "unit": "kWh", "mode": "estimated", "explanation": "..." },
        "daily_billed_kwh_estimate":  { "value": 99838.71,  "unit": "kWh/day", "mode": "estimated", "explanation": "..." },
        "daily_energy_delivered_mwh": { "value": 25.81,     "unit": "MWh/day", "mode": "meter",  "explanation": "..." },
        "energy_delivered_kwh":       { "value": 800110.0,  "unit": "kWh", "mode": "meter",     "explanation": "..." },
        "energy_delivered_vs_billed": { "value": { "delivered_kwh": 800110.0, "actual_billed_kwh": 3095000.0, "projected_billed_kwh": 3095000.0, "gap_kwh": -2294890.0 }, "unit": "kWh", "mode": "meter", "explanation": "..." }
      },
      "revenue":     { ... },
      "performance": { "coverage_rate": {...}, "billing_efficiency": {...}, "atc_loss": {...}, ... },
      "managers":    { ... }
    },
    ...
  ]
}
```

### Response — single feeder
Same as one item above **plus** `period` at the top level **and** a `customers_list` section showing every commercial customer on that feeder:

```json
{
  "period": { ... },
  "feeder":      { "slug": "kn-tam-coc", "name": "COCA COLA", "voltage_level": "11kv", "feeder_class": "MDI", "district": {...}, "state": {...} },
  "customers":   { "total": {...}, "mdi": {...}, "mdni": {...} },
  "energy":      { "energy_consumed_kwh": {...}, "actual_billed_kwh": {...}, "energy_delivered_kwh": {...}, ... },
  "revenue":     { ... },
  "performance": { ... },
  "managers":    { ... },

  "customers_list": {
    "count": 14,
    "customers": [
      {
        "id":               123,
        "external_id":      "32214922-abc",
        "account_no":       "32/25/90/0517-01",
        "meter_number":     "252424078",
        "customer_name":    "WEST AFRICAN TANNERY",
        "customer_address": "PLOT 53 CHALLAWA INDUSTRIAL ESTATE KANO",
        "phone_number":     "8028647304",
        "customer_type":    "MDI",
        "is_bypass":        false
      },
      {
        "id":               124,
        "external_id":      "...",
        "account_no":       "32/25/90/0518-01",
        "meter_number":     "252424079",
        "customer_name":    "DANGOTE CEMENT PLC",
        "customer_address": "KM 15 BICHI ROAD KANO",
        "phone_number":     "8012345678",
        "customer_type":    "MDI",
        "is_bypass":        false
      },
      ...
    ]
  }
}
```

> **UI tip:** When a user clicks a feeder, call `/api/commercial/feeders/<slug>/` — the `customers_list` gives you every customer on that feeder immediately. You can then let users click individual customers to see their readings via `/api/commercial/customers/<id>/`.

---

## 11. Service Bands

### List all bands (A–E)
```
GET /api/commercial/bands/
GET /api/commercial/bands/?mode=monthly&year=2026&month=1
GET /api/commercial/bands/?type=MDI
```

### Single band
```
GET /api/commercial/bands/<slug>/
GET /api/commercial/bands/a/?mode=monthly&year=2026&month=1
```
**Slugs:** `a` `b` `c` `d` `e`

### Response — list
```json
{
  "period": { ... },
  "count": 5,
  "bands": [
    {
      "band": { "slug": "a", "name": "A", "description": "" },
      "customers":   { "total": {...}, "mdi": {...}, "mdni": {...}, "bypass_count": {...} },
      "energy": {
        "energy_consumed_kwh":  { "value": 20100000.0, "unit": "kWh", "mode": "actual", "explanation": "..." },
        "actual_billed_kwh":    { "value": 20050000.0, "unit": "kWh", "mode": "actual", "explanation": "..." },
        "energy_delivered_kwh": { "value": 8200000.0,  "unit": "kWh", "mode": "mixed",  "explanation": "..." },
        ...
      },
      "revenue":     { ... },
      "performance": { "coverage_rate": {...}, "billing_efficiency": {...}, "atc_loss": {...}, ... },
      "managers":    { ... }
    },
    { "band": { "slug": "b", "name": "B" }, ... },
    { "band": { "slug": "c", "name": "C" }, ... },
    { "band": { "slug": "d", "name": "D" }, ... },
    { "band": { "slug": "e", "name": "E" }, ... }
  ]
}
```

---

## 12. Customers

### List customers (paginated)
```
GET /api/commercial/customers/
GET /api/commercial/customers/?feeder=kn-tam-coc&mode=monthly&year=2026&month=1
GET /api/commercial/customers/?state=KN&type=MDI
GET /api/commercial/customers/?search=dangote
```

| Filter | Description |
|---|---|
| `search` | Search by name, account number, or meter number |
| `feeder` | Feeder slug |
| `district` | District slug |
| `state` | State slug |
| `type` | `MDI` or `MDNI` |
| `page` | Page number (default `1`) |
| `page_size` | Results per page (default `50`, max `200`) |

### Response
```json
{
  "period": { ... },
  "pagination": { "total": 1871, "page": 1, "page_size": 50, "pages": 38 },
  "customers": [
    {
      "id":               "1011e552-e6b0-4c46-b342-4ea0abaa9b3f",
      "account_no":       "32/25/90/0517-01",
      "meter_number":     "252424078",
      "customer_name":    "WEST AFRICAN TANNERY",
      "customer_type":    "MDI",
      "customer_address": "PLOT 53 CHALLAWA INDUSTRIAL ESTATE KANO",
      "phone_number":     "8028647304",
      "is_bypass":        false,
      "feeder":   { "slug": "kn-tam-coc", "name": "COCA COLA" },
      "district": { "slug": "KN-IDU",     "name": "Kano Industrial" },
      "state":    { "slug": "KN",          "name": "Kano" },
      "period_billing": {
        "readings_count":    2,
        "total_billed_kwh":  150.0,
        "energy_charge":     31425.0,
        "vat":               2356.88,
        "total_billed":      33781.88,
        "last_reading_date": "2026-01-28"
      }
    },
    ...
  ]
}
```

---

### Customer detail + readings
```
GET /api/commercial/customers/<id>/
GET /api/commercial/customers/<id>/?mode=monthly&year=2026&month=1
```

```json
{
  "period": { ... },
  "customer": {
    "id":               "1011e552-...",
    "external_id":      "32214922-...",
    "account_no":       "32/25/90/0517-01",
    "meter_number":     "252424078",
    "customer_name":    "WEST AFRICAN TANNERY",
    "customer_type":    "MDI",
    "customer_address": "PLOT 53 CHALLAWA INDUSTRIAL ESTATE KANO",
    "phone_number":     "8028647304",
    "is_bypass":        false,
    "feeder":   { "slug": "kn-tam-coc", "name": "COCA COLA" },
    "district": { "slug": "KN-IDU",     "name": "Kano Industrial" },
    "state":    { "slug": "KN",          "name": "Kano" }
  },
  "period_billing": {
    "readings_count":    2,
    "total_billed_kwh":  150.0,
    "energy_charge":     31425.0,
    "vat":               2356.88,
    "total_billed":      33781.88,
    "last_reading_date": "2026-01-28"
  },
  "readings": [
    {
      "id":                   "uuid",
      "reading_date":         "2026-01-28",
      "reading_type":         "MDI",
      "previous_reading":     1200.0,
      "present_reading":      1280.0,
      "consumption":          80.0,
      "billed_consumption":   80.0,
      "tariff_rate":          209.5,
      "energy_charge":        16760.0,
      "vat":                  1257.0,
      "total_billed":         18017.0,
      "has_proof":            true,
      "recorded_by":          "Musa Aliyu",
      "observation":          "",

      "gis": {
        "gis_id":    "GIS-00412",
        "gis_match": true
      },

      "ocr": {
        "ocr_status":          "matched",
        "ocr_extracted_value": 1280.0,
        "ocr_confidence":      98.4
      },

      "audit": {
        "audit_status": "approved",
        "audited_by":   "Amina Garba",
        "audit_note":   "",
        "audited_at":   "2026-01-29T08:14:22Z"
      }
    }
  ]
}

### Reading field reference

#### Core billing fields
| Field | Type | Description |
|---|---|---|
| `id` | UUID | Reading record ID |
| `reading_date` | `YYYY-MM-DD` | Date reading was submitted |
| `reading_type` | `"MDI"` / `"MDNI"` | Customer type at time of reading |
| `previous_reading` | number | Previous meter register value |
| `present_reading` | number | Current meter register value |
| `consumption` | number | `present − previous` (raw delta) |
| `billed_consumption` | number | Consumption used for billing (may differ after corrections) |
| `tariff_rate` | number | NGN per kWh |
| `energy_charge` | number | `billed_consumption × tariff_rate` (NGN) |
| `vat` | number | 7.5% of energy_charge (NGN) |
| `total_billed` | number | `energy_charge + vat` (NGN) |
| `has_proof` | boolean | Whether a photo was attached to this reading |
| `recorded_by` | string | Name of the field officer who submitted the reading |
| `observation` | string | Free-text note from the field officer |

#### `gis` block — Geographic verification
| Field | Type | Description |
|---|---|---|
| `gis_id` | string \| null | GIS location identifier matched to this reading |
| `gis_match` | boolean \| null | Whether the reading location matched the GIS record |

#### `ocr` block — Optical Character Recognition verification
| Field | Type | Values | Description |
|---|---|---|---|
| `ocr_status` | string | `pending` `matched` `mismatch` `failed` `skipped` | OCR verification outcome |
| `ocr_extracted_value` | number \| null | — | Meter value extracted by OCR from the proof photo |
| `ocr_confidence` | number \| null | 0–100 | OCR confidence score (%) |

**OCR status values:**
- `pending` — photo uploaded, OCR not yet run
- `matched` — OCR extracted value agrees with submitted reading
- `mismatch` — OCR value differs from the reading — warrants human review
- `failed` — OCR could not read the image
- `skipped` — no proof photo, OCR not applicable

#### `audit` block — Human audit trail
| Field | Type | Values | Description |
|---|---|---|---|
| `audit_status` | string | `pending` `approved` `rejected` | Audit outcome |
| `audited_by` | string \| null | — | Name of the auditor who reviewed this reading |
| `audit_note` | string | — | Reason for approval or rejection |
| `audited_at` | ISO 8601 \| null | — | Timestamp of the audit action |

**Audit status values:**
- `pending` — not yet reviewed
- `approved` — auditor confirmed reading is valid
- `rejected` — auditor flagged reading as invalid (check `audit_note` for reason)

> **UI tip:** Show `ocr_status === "mismatch"` and `audit_status === "rejected"` with a warning badge on the reading row. These readings may need correction before billing is finalised.
```

---

### Top / Bottom N customers
```
GET /api/commercial/customers/top/                           ← top 10 by billing
GET /api/commercial/customers/top/?n=50                     ← top 50
GET /api/commercial/customers/top/?order=bottom&n=10        ← bottom 10 (lowest billing)
GET /api/commercial/customers/top/?order=bottom&n=50        ← bottom 50
GET /api/commercial/customers/top/?n=10&state=KN&type=MDI
GET /api/commercial/customers/top/?n=20&order=bottom&feeder=kn-tam-coc
```

| Parameter | Default | Max |
|---|---|---|
| `n` | `10` | `50` |
| `order` | `top` | `top` or `bottom` |

> `order=top` → highest billed customers in the period
> `order=bottom` → lowest billed customers — useful for identifying flat-liners and non-billers

```json
{
  "period": { ... },
  "n": 10,
  "order": "top",
  "customers": [
    {
      "id":            "uuid",
      "account_no":    "32/25/90/0517-01",
      "meter_number":  "252424078",
      "customer_name": "DANGOTE CEMENT PLC",
      "customer_type": "MDI",
      "feeder":   { "slug": "...", "name": "..." },
      "district": { "slug": "...", "name": "..." },
      "state":    { "slug": "...", "name": "..." },
      "period_billing": {
        "readings_count":    3,
        "total_billed_kwh":  450.0,
        "energy_charge":     94275.0,
        "vat":               7070.63,
        "total_billed":      101345.63,
        "last_reading_date": "2026-01-28"
      }
    },
    ...
  ]
}
```

---

## 13. Trend — Last 4 Periods

> Returns current period + 4 previous periods for 8 KPIs. One DB query — fast at any scope.

```
GET /api/commercial/trend/
GET /api/commercial/trend/?mode=monthly&year=2026&month=1
GET /api/commercial/trend/?mode=monthly&year=2026&month=1&state=KN
GET /api/commercial/trend/?mode=monthly&year=2026&month=1&type=MDI&feeder=kn-tam-coc
GET /api/commercial/trend/?mode=yearly&year=2026
```

### Scope filters
| Parameter | Description |
|---|---|
| `type` | `MDI` or `MDNI` |
| `state` | State slug — trend for one state |
| `district` | District slug |
| `feeder` | Feeder slug |

### Response
```json
{
  "current_period": {
    "mode":       "monthly",
    "start_date": "2026-01-01",
    "end_date":   "2026-01-31",
    "label":      "January 2026",
    "days":       31
  },
  "total_customers": 1871,
  "count": 5,
  "periods": [
    {
      "period": {
        "mode":       "monthly",
        "start_date": "2025-09-01",
        "end_date":   "2025-09-30",
        "label":      "September 2025",
        "days":       30,
        "is_current": false
      },
      "actual_billed_kwh":   { "value": 31200000.0,    "unit": "kWh", "mode": "actual", "explanation": "Energy billed from real meter readings in this period." },
      "energy_consumed_kwh": { "value": 31150000.0,    "unit": "kWh", "mode": "actual", "explanation": "Sum of (present_reading - previous_reading) in this period." },
      "actual_total_billed": { "value": 6900000000.0,  "unit": "NGN", "mode": "actual", "explanation": "Total revenue billed including VAT." },
      "energy_charge":       { "value": 6418604651.16, "unit": "NGN", "mode": "actual", "explanation": "Revenue excluding VAT." },
      "vat":                 { "value": 481395348.84,  "unit": "NGN", "mode": "actual", "explanation": "7.5% VAT." },
      "customers_read":      { "value": 1148,          "unit": "",    "mode": "actual", "explanation": "Distinct customers with a reading in this period." },
      "coverage_rate":       { "value": 61.36,         "unit": "%",   "mode": "actual", "explanation": "customers_read / total_registered × 100." },
      "arpu":                { "value": 6010453.4,     "unit": "NGN", "mode": "actual", "explanation": "Average Revenue Per Customer." }
    },
    { "period": { "label": "October 2025",  "is_current": false }, ... },
    { "period": { "label": "November 2025", "is_current": false }, ... },
    { "period": { "label": "December 2025", "is_current": false }, ... },
    {
      "period": { "label": "January 2026", "is_current": true },
      "actual_billed_kwh":   { "value": 35069892.61, ... },
      "energy_consumed_kwh": { "value": 35069892.61, ... },
      ...
    }
  ]
}
```

> Periods are always **oldest → newest**. The last item always has `is_current: true`. Use this to draw bar or line trend charts.

---

## 14. KPI Reference

### `energy_consumed_kwh` — in every view

This field appears inside `energy` on **every** level: overview, states, districts, feeders, bands, and per period on trend.

```
energy_consumed_kwh = SUM(present_reading − previous_reading)
```

It is the raw meter register delta — actual physical consumption recorded on the meter, not necessarily what was billed.

### `energy` section (all levels)
| Key | Unit | Mode | Description |
|---|---|---|---|
| `energy_consumed_kwh` | kWh | actual | Raw meter consumption: SUM(present − previous) |
| `actual_billed_kwh` | kWh | actual | Energy from real readings (billed_consumption) |
| `estimated_billed_kwh` | kWh | estimated | Projected for unread customers (last avg × days) |
| `total_projected_billed_kwh` | kWh | estimated | actual + estimated |
| `daily_billed_kwh_estimate` | kWh/day | estimated | Actual billed ÷ days |
| `daily_energy_delivered_mwh` | MWh/day | meter/system/mixed | Total delivered ÷ days |
| `energy_delivered_kwh` | kWh | meter/system/mixed | Total energy injected for this period |
| `energy_delivered_vs_billed` | object | meter/system/mixed | `{delivered_kwh, actual_billed_kwh, projected_billed_kwh, gap_kwh}` |

### `revenue` section
| Key | Unit | Mode | Description |
|---|---|---|---|
| `actual_energy_charge` | NGN | actual | Energy charge excl. VAT from real readings |
| `estimated_energy_charge` | NGN | estimated | Projected energy charge for unread customers |
| `actual_vat` | NGN | actual | 7.5% VAT on actual energy charge |
| `actual_total_billed` | NGN | actual | Total billed = energy_charge + VAT |
| `estimated_revenue` | NGN | estimated | Revenue at risk from unread customers (incl. VAT) |
| `total_projected_revenue` | NGN | estimated | actual_total_billed + estimated_revenue |
| `mdi_revenue_split` | % | actual | % of actual revenue from MDI customers |
| `mdni_revenue_split` | % | actual | % of actual revenue from MDNI customers |
| `arpu` | NGN | actual | Average Revenue Per Customer = total_billed ÷ customers_read |

### `customers` block (all levels)
| Key | Unit | Mode | Description |
|---|---|---|---|
| `total` | — | actual | All registered customers (MDI + MDNI) |
| `mdi` | — | actual | Maximum Demand Installation customers |
| `mdni` | — | actual | Non Maximum Demand customers |
| `bypass_count` | — | actual | Customers flagged for meter bypass / tampering — highlight in UI |

### `performance` section
| Key | Unit | Mode | Description |
|---|---|---|---|
| `coverage_rate` | % | actual | % of customers with at least one reading |
| `customers_read` | — | actual | Count of customers with a reading |
| `unread_customers` | — | actual | Customers with no reading — revenue at risk |
| `billing_efficiency` | % | estimated | `(energy_billed / energy_delivered) × 100` — show N/A if > 100% |
| `atc_loss` | % | estimated | `(1 − billed/delivered) × 100` — show N/A if negative |

### `energy_breakdown` section (overview, single state, single district)
| Field | Description |
|---|---|
| `energy_delivered_kwh` | Grid energy delivered to this group |
| `energy_consumed_kwh` | Meter register consumption for this group |
| `actual_billed_kwh` | Formally billed energy for this group |
| `atc_loss` | `(1 − billed/delivered) × 100` — `null` when delivered is 0 |
| `mode` | `meter` / `system` / `mixed` |

### `trend` (per period)
| Key | Unit | Description |
|---|---|---|
| `actual_billed_kwh` | kWh | Energy billed in this period |
| `energy_consumed_kwh` | kWh | Raw meter consumption (present − previous) |
| `actual_total_billed` | NGN | Revenue including VAT |
| `energy_charge` | NGN | Revenue excluding VAT |
| `vat` | NGN | VAT component |
| `customers_read` | — | Distinct customers with a reading |
| `coverage_rate` | % | customers_read / total_registered × 100 |
| `arpu` | NGN | Average Revenue Per Customer |

---

## 15. Quick Reference — All Endpoints

```
GET /api/commercial/overview/                                System-wide KPIs + energy_breakdown by state/district/band
GET /api/commercial/overview/?type=MDI                       MDI customers only
GET /api/commercial/overview/?mode=monthly&year=2026&month=1 January 2026

GET /api/commercial/trend/                                   Current + last 4 periods (8 KPIs)
GET /api/commercial/trend/?state=KN                         Scoped to Kano
GET /api/commercial/trend/?feeder=kn-tam-coc                Scoped to a feeder

GET /api/commercial/states/                                  All states + full metrics
GET /api/commercial/states/?type=MDI                         MDI only
GET /api/commercial/states/KN/                              Single state + energy_breakdown.by_district

GET /api/commercial/districts/                               All districts + full metrics
GET /api/commercial/districts/?state=KN                      Districts in Kano
GET /api/commercial/districts/KN-IDU/                       Single district + energy_breakdown.by_feeder

GET /api/commercial/feeders/                                 Feeders with commercial customers only
GET /api/commercial/feeders/?state=KN                        Feeders in Kano
GET /api/commercial/feeders/?district=KN-IDU                 Feeders in a district
GET /api/commercial/feeders/<slug>/                         Single feeder + customers_list

GET /api/commercial/bands/                                   All service bands A–E
GET /api/commercial/bands/a/                                 Single band detail

GET /api/commercial/customers/                               Paginated customer list
GET /api/commercial/customers/?search=dangote                Customer search
GET /api/commercial/customers/?feeder=kn-tam-coc            Customers on a specific feeder
GET /api/commercial/customers/?state=KN&type=MDI             Filtered list
GET /api/commercial/customers/top/                           Top 10 by billing
GET /api/commercial/customers/top/?n=50                      Top 50
GET /api/commercial/customers/top/?order=bottom&n=10         Bottom 10 (lowest billing)
GET /api/commercial/customers/top/?order=bottom&n=50         Bottom 50
GET /api/commercial/customers/<id>/                          Customer detail + readings
```

---

*KEDCO Raven Commercial Analytics Module — staging environment.*
