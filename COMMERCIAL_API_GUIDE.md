# KEDCO Raven — Commercial Analytics API
### Frontend Integration Guide

**Base URL (staging):** `https://staging.apiraven.raven-emrc.com/api/commercial/`
**Auth:** Bearer token in `Authorization` header (same as existing Raven auth)
**All responses:** `application/json`

---

## Table of Contents
1. [Global Query Parameters](#1-global-query-parameters)
2. [Metric Object Shape](#2-metric-object-shape)
3. [Overview](#3-overview)
4. [States](#4-states)
5. [Districts](#5-districts)
6. [Feeders](#6-feeders)
7. [Service Bands](#7-service-bands)
8. [Customers](#8-customers)
9. [Trend — Last 4 Periods](#9-trend--last-4-periods)
10. [Data Modes](#10-data-modes)
11. [KPI Reference](#11-kpi-reference)

---

## 1. Global Query Parameters

These work on **every** endpoint unless stated otherwise.

| Parameter | Values | Default | Description |
|---|---|---|---|
| `mode` | `daily` `weekly` `monthly` `yearly` | `monthly` | Time window mode |
| `year` | e.g. `2026` | current year | Used with `monthly` / `yearly` |
| `month` | `1`–`12` | current month | Used with `monthly` |
| `from_date` | `YYYY-MM-DD` | today | Used with `daily` / `weekly` |
| `feeder_type` | `11kv` `33kv` | *(all)* | **Global filter.** Scopes the entire response to feeders of a specific voltage level. Applies at all levels — overview, states, districts, feeders, bands, and customers. |
| `type` | `MDI` `MDNI` | *(all)* | **Sub-level filter.** Use when drilling into a specific customer segment within a geographic scope (e.g. MDI customers in Kano, or MDNI customers on a specific feeder). Not intended for top-level overview. |

> **MDI** = Maximum Demand Industrial. **MDNI** = Non Maximum Demand Industrial.

### Time Mode Examples
```
?mode=monthly&year=2026&month=1          → January 2026
?mode=daily&from_date=2026-03-15         → March 15 2026
?mode=weekly&from_date=2026-03-10        → Week starting March 10
?mode=yearly&year=2026                   → Full year 2026
?mode=monthly&year=2026&month=1&type=MDI → January 2026, MDI customers only
```

> **Important:** Always pass an explicit period. Defaulting to the current month will return 0 for all metrics if readings haven't been submitted yet for this month.

---

## 2. Metric Object Shape

**Every single KPI** in the API is returned in this consistent wrapper:

```json
{
  "value": 1871,
  "unit": "",
  "mode": "actual",
  "explanation": "Total registered MDI and MDNI customers across all feeders."
}
```

| Field | Type | Description |
|---|---|---|
| `value` | `number` or `object` | The KPI value |
| `unit` | `string` | `""` `"kWh"` `"kWh/day"` `"MWh/day"` `"NGN"` `"%"` |
| `mode` | `string` | See [Data Modes](#10-data-modes) |
| `explanation` | `string` | Always present — use as tooltip text on every KPI card |

> **UI Tip:** Use `mode` to visually differentiate values. Show `"actual"` as solid/confirmed, flag `"estimated"` with ≈ or dashed border, and show `"meter"` / `"system"` / `"mixed"` as a small badge on energy delivered metrics.

---

## 3. Overview

> Full system-wide commercial metrics.

```
GET /api/commercial/overview/
```

### Response Structure
```json
{
  "period": {
    "mode": "monthly",
    "start_date": "2026-01-01",
    "end_date": "2026-01-31",
    "label": "January 2026",
    "days": 31
  },
  "customers": {
    "total":  { "value": 1871, "unit": "", "mode": "actual", "explanation": "..." },
    "mdi":    { "value": 1157, "unit": "", "mode": "actual", "explanation": "..." },
    "mdni":   { "value": 714,  "unit": "", "mode": "actual", "explanation": "..." }
  },
  "energy": {
    "energy_consumed_kwh":        { "value": 35069892.61,  "unit": "kWh",     "mode": "actual",    "explanation": "..." },
    "actual_billed_kwh":          { "value": 35069892.61,  "unit": "kWh",     "mode": "actual",    "explanation": "..." },
    "estimated_billed_kwh":       { "value": 0.0,          "unit": "kWh",     "mode": "estimated", "explanation": "..." },
    "total_projected_billed_kwh": { "value": 35069892.61,  "unit": "kWh",     "mode": "estimated", "explanation": "..." },
    "daily_billed_kwh_estimate":  { "value": 1131931.0,    "unit": "kWh/day", "mode": "estimated", "explanation": "..." },
    "daily_energy_delivered_mwh": { "value": 502.5,        "unit": "MWh/day", "mode": "mixed",     "explanation": "..." },
    "energy_delivered_kwh":       { "value": 15578100.0,   "unit": "kWh",     "mode": "mixed",     "explanation": "..." },
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
    "billing_efficiency": { "value": 225.1, "unit": "%", "mode": "estimated", "explanation": "..." },
    "atc_loss":           { "value": -125.1,"unit": "%", "mode": "estimated", "explanation": "..." }
  },
  "managers": {
    "total_mdi_managers":  { "value": 60, "unit": "", "mode": "actual", "explanation": "..." },
    "total_mdni_managers": { "value": 0,  "unit": "", "mode": "actual", "explanation": "..." }
  }
}
```

---

## 4. States

### List all states
```
GET /api/commercial/states/
GET /api/commercial/states/?mode=monthly&year=2026&month=1
```

### Single state
```
GET /api/commercial/states/<slug>/
```
**Slug examples:** `KN` `JG` `KS`

### Response (list)
```json
{
  "period": { ... },
  "count": 3,
  "states": [
    {
      "state": { "slug": "KN", "name": "Kano" },
      "customers":   { "total": {...}, "mdi": {...}, "mdni": {...} },
      "energy": {
        "energy_consumed_kwh":        { "value": 33490517.61, "unit": "kWh", "mode": "actual",  "explanation": "..." },
        "actual_billed_kwh":          { "value": 33552126.86, "unit": "kWh", "mode": "actual",  "explanation": "..." },
        "estimated_billed_kwh":       { "value": 0.0,         "unit": "kWh", "mode": "estimated","explanation": "..." },
        "total_projected_billed_kwh": { "value": 33552126.86, "unit": "kWh", "mode": "estimated","explanation": "..." },
        "daily_billed_kwh_estimate":  { "value": 1082326.67,  "unit": "kWh/day","mode": "estimated","explanation": "..." },
        "daily_energy_delivered_mwh": { "value": 250.93,      "unit": "MWh/day","mode": "mixed",  "explanation": "..." },
        "energy_delivered_kwh":       { "value": 7778810.0,   "unit": "kWh", "mode": "mixed",   "explanation": "..." },
        "energy_delivered_vs_billed": {
          "value": {
            "delivered_kwh": 7778810.0,
            "actual_billed_kwh": 33552126.86,
            "projected_billed_kwh": 33552126.86,
            "gap_kwh": -25773316.86
          },
          "unit": "kWh", "mode": "mixed", "explanation": "..."
        }
      },
      "revenue":     { "actual_total_billed": {...}, "total_projected_revenue": {...}, "estimated_revenue": {...}, ... },
      "performance": { "coverage_rate": {...}, "customers_read": {...}, "atc_loss": {...}, ... },
      "managers":    { "total_mdi_managers": {...}, "total_mdni_managers": {...} }
    },
    ...
  ]
}
```

### Single state response
Same shape as one item above, plus `period` at the top level.

---

## 5. Districts

### List all districts
```
GET /api/commercial/districts/
GET /api/commercial/districts/?state=KN
```

### Single district
```
GET /api/commercial/districts/<slug>/
```
**Slug examples:** `KN-IDU` `KN-NW` `JG-DUT`

### Response (list)
```json
{
  "period": { ... },
  "count": 17,
  "districts": [
    {
      "district": { "slug": "KN-IDU", "name": "Kano Industrial", "state": "Kano" },
      "customers":   { ... },
      "energy":      { ... },
      "revenue":     { ... },
      "performance": { ... },
      "managers":    { ... }
    },
    ...
  ]
}
```

---

## 6. Feeders

### List all feeders
```
GET /api/commercial/feeders/
GET /api/commercial/feeders/?state=KN
GET /api/commercial/feeders/?district=KN-IDU
GET /api/commercial/feeders/?state=KN&type=MDI
```

### Single feeder
```
GET /api/commercial/feeders/<slug>/
```
**Slug examples:** `kn-tam-coc` `kn-nw-abr`

### Response (list)
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
      "customers":   { ... },
      "energy":      { ... },
      "revenue":     { ... },
      "performance": { ... },
      "managers":    { ... }
    },
    ...
  ]
}
```

---

## 7. Service Bands

### List all bands (A–E)
```
GET /api/commercial/bands/
```

### Single band
```
GET /api/commercial/bands/<slug>/
```
**Slugs:** `a` `b` `c` `d` `e`

### Response (list)
```json
{
  "period": { ... },
  "count": 5,
  "bands": [
    {
      "band": { "slug": "a", "name": "A", "description": "" },
      "customers":   { ... },
      "energy":      { ... },
      "revenue":     { ... },
      "performance": { ... },
      "managers":    { ... }
    },
    ...
  ]
}
```

---

## 8. Customers

### List customers (paginated)
```
GET /api/commercial/customers/
```

#### Filters
| Parameter | Description |
|---|---|
| `search` | Search by customer name, account number, or meter number |
| `feeder` | Feeder slug — use this to show all customers on a feeder |
| `district` | District slug |
| `state` | State slug |
| `page` | Page number (default `1`) |
| `page_size` | Results per page (default `50`, max `200`) |

> **Feeder drill-down:** When a user clicks a feeder, call:
> `GET /api/commercial/customers/?feeder=<feeder-slug>&mode=monthly&year=2026&month=1`

#### Example requests
```
GET /api/commercial/customers/?search=dangote
GET /api/commercial/customers/?type=MDI&state=KN&page=2
GET /api/commercial/customers/?feeder=kn-tam-coc&mode=monthly&year=2026&month=1
```

#### Response
```json
{
  "period": { ... },
  "pagination": {
    "total": 1871,
    "page": 1,
    "page_size": 50,
    "pages": 38
  },
  "customers": [
    {
      "id":               "1011e552-e6b0-4c46-b342-4ea0abaa9b3f",
      "account_no":       "32/25/90/0517-01",
      "meter_number":     "252424078",
      "customer_name":    "WEST AFRICAN TANNERY",
      "customer_type":    "MDI",
      "customer_address": "PLOT 53 CHALLAWA INDUSTRIAL ESTATE KANO",
      "phone_number":     "8028647304",
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

### Customer detail
```
GET /api/commercial/customers/<uuid>/
```

#### Response
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
      "id":                 "uuid",
      "reading_date":       "2026-01-28",
      "reading_type":       "MDI",
      "previous_reading":   1200.0,
      "present_reading":    1280.0,
      "consumption":        80.0,
      "billed_consumption": 80.0,
      "tariff_rate":        209.5,
      "energy_charge":      16760.0,
      "vat":                1257.0,
      "total_billed":       18017.0,
      "has_proof":          true,
      "recorded_by":        "Musa Aliyu",
      "observation":        ""
    },
    ...
  ]
}
```

---

### Top / Bottom N customers
```
GET /api/commercial/customers/top/                              ← top 10 by default
GET /api/commercial/customers/top/?n=50                        ← top 50
GET /api/commercial/customers/top/?order=bottom&n=10           ← bottom 10
GET /api/commercial/customers/top/?order=bottom&n=50           ← bottom 50
GET /api/commercial/customers/top/?n=10&state=KN&type=MDI
GET /api/commercial/customers/top/?n=20&order=bottom&feeder=kn-tam-coc
```

| Parameter | Default | Options | Max |
|---|---|---|---|
| `n` | `10` | any integer | `50` |
| `order` | `top` | `top` \| `bottom` | — |
| `state` | — | State slug | — |
| `district` | — | District slug | — |
| `feeder` | — | Feeder slug | — |

- `order=top` → highest billed customers
- `order=bottom` → lowest billed customers (potential flat-liners / non-billers)

#### Response
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
> Results are sorted by `period_billing.total_billed` — descending for `top`, ascending for `bottom`.

---

## 9. Trend — Last 4 Periods

> Returns current period + 4 previous periods for 8 key KPIs.
> **One DB query** — fast regardless of scope.

```
GET /api/commercial/trend/
```

### Filters (same as all other endpoints)
| Parameter | Description |
|---|---|
| `mode` / `year` / `month` / `from_date` | Time period (current) |
| `type` | `MDI` or `MDNI` |
| `feeder_type` | `11kv` or `33kv` |
| `state` | State slug — trend for one state |
| `district` | District slug — trend for one district |
| `feeder` | Feeder slug — trend for one feeder |

### Example requests
```
GET /api/commercial/trend/?mode=monthly&year=2026&month=1
GET /api/commercial/trend/?mode=monthly&year=2026&month=1&state=KN
GET /api/commercial/trend/?mode=monthly&year=2026&month=1&type=MDI&feeder=kn-tam-coc
GET /api/commercial/trend/?mode=yearly&year=2026
```

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
      "actual_billed_kwh":   { "value": 31200000.0, "unit": "kWh", "mode": "actual", "explanation": "..." },
      "energy_consumed_kwh": { "value": 31150000.0, "unit": "kWh", "mode": "actual", "explanation": "..." },
      "actual_total_billed": { "value": 6900000000.0, "unit": "NGN", "mode": "actual", "explanation": "..." },
      "energy_charge":       { "value": 6418604651.16, "unit": "NGN", "mode": "actual", "explanation": "..." },
      "vat":                 { "value": 481395348.84,  "unit": "NGN", "mode": "actual", "explanation": "..." },
      "customers_read":      { "value": 1148, "unit": "", "mode": "actual", "explanation": "..." },
      "coverage_rate":       { "value": 61.36, "unit": "%", "mode": "actual", "explanation": "..." },
      "arpu":                { "value": 6010453.4, "unit": "NGN", "mode": "actual", "explanation": "..." }
    },
    { "period": { "label": "October 2025",  "is_current": false }, ... },
    { "period": { "label": "November 2025", "is_current": false }, ... },
    { "period": { "label": "December 2025", "is_current": false }, ... },
    {
      "period": { "label": "January 2026", "is_current": true },
      "actual_billed_kwh":   { "value": 35069892.61, ... },
      ...
    }
  ]
}
```

> **Periods are always oldest → newest.** The last item always has `is_current: true`.
> Use this endpoint to draw trend charts — bar, line, or sparkline.

---

## 10. Data Modes

Every metric carries a `mode` field:

| Mode | Meaning | UI Treatment |
|---|---|---|
| `"actual"` | Computed from real meter readings | Show as solid/confirmed value |
| `"estimated"` | Projected using last known daily average for unread customers | Show with ≈ prefix, dashed border, or italic |
| `"meter"` | Energy delivered — sourced from actual injection substation meters | Show as confirmed (green badge) |
| `"system"` | Energy delivered — estimated from HourlyLoad data (no meter data available) | Show as estimated (amber badge) |
| `"mixed"` | Energy delivered — some feeders used meter, some used system estimate | Show as partial (blue badge) |

### Estimation logic
- **Who gets estimated?** Customers with zero readings in the selected period.
- **How?** `last_billed_consumption ÷ 7 × period_days` = daily average × days.

### Energy delivered logic
- **PRIMARY:** `EnergyDelivered` table — actual meter sum for the period. Used if data exists and no single day exceeds 500 MWh (outlier guard).
- **FALLBACK:** `HourlyLoad` table — `avg_load_mw × supply_hours`. Used when meter data is missing or suspect.
- `mode` tells you which source was used per feeder/state/district.

### When energy delivered = 0
No `EnergyDelivered` or `HourlyLoad` data exists for those feeders in the technical module. This is a data gap — the code is correct.

### When ATC loss is negative
Means `energy_billed > energy_delivered` — either the technical data is incomplete (most likely) or there are billing adjustments. Display as `N/A` if negative.

---

## 11. KPI Reference

### customers
| Key | Unit | Mode | Description |
|---|---|---|---|
| `total` | — | actual | Total MDI + MDNI customers |
| `mdi` | — | actual | Maximum Demand Industrial customers |
| `mdni` | — | actual | Non Maximum Demand Industrial customers |

### energy
| Key | Unit | Mode | Description |
|---|---|---|---|
| `energy_consumed_kwh` | kWh | actual | Sum of (present_reading − previous_reading) for all customers read — raw meter consumption |
| `actual_billed_kwh` | kWh | actual | Energy from real readings (billed_consumption field) |
| `estimated_billed_kwh` | kWh | estimated | Projected energy for unread customers |
| `total_projected_billed_kwh` | kWh | estimated | actual + estimated |
| `daily_billed_kwh_estimate` | kWh/day | estimated | Actual billed ÷ days in period |
| `daily_energy_delivered_mwh` | MWh/day | meter/system/mixed | Total delivered ÷ days — for display only |
| `energy_delivered_kwh` | kWh | meter/system/mixed | Total energy injected into feeders for this period |
| `energy_delivered_vs_billed` | kWh | meter/system/mixed | Object: `delivered_kwh`, `actual_billed_kwh`, `projected_billed_kwh`, `gap_kwh` |

### revenue
| Key | Unit | Mode | Description |
|---|---|---|---|
| `actual_energy_charge` | NGN | actual | Energy charge from real readings (excl. VAT) |
| `estimated_energy_charge` | NGN | estimated | Projected energy charge for unread customers |
| `actual_vat` | NGN | actual | 7.5% VAT on actual energy charge |
| `actual_total_billed` | NGN | actual | Total billed = energy_charge + VAT |
| `estimated_revenue` | NGN | estimated | Revenue at risk from unread customers (incl. VAT) |
| `total_projected_revenue` | NGN | estimated | actual_total_billed + estimated_revenue |
| `mdi_revenue_split` | % | actual | % of actual revenue from MDI customers |
| `mdni_revenue_split` | % | actual | % of actual revenue from MDNI customers |
| `arpu` | NGN | actual | Average Revenue Per Customer = actual_total_billed ÷ customers_read |

### performance
| Key | Unit | Mode | Description |
|---|---|---|---|
| `coverage_rate` | % | actual | % of customers with a reading in this period |
| `customers_read` | — | actual | Count of customers with at least one reading |
| `unread_customers` | — | actual | Customers with no reading — revenue at risk |
| `billing_efficiency` | % | estimated | energy_billed ÷ energy_delivered × 100. Show N/A if > 100% or negative |
| `atc_loss` | % | estimated | 100 − billing_efficiency. Show N/A if negative |

### managers
| Key | Unit | Mode | Description |
|---|---|---|---|
| `total_mdi_managers` | — | actual | MDI field officers with active assignments |
| `total_mdni_managers` | — | actual | MDNI field officers with active assignments |

### trend (per period)
| Key | Unit | Mode | Description |
|---|---|---|---|
| `actual_billed_kwh` | kWh | actual | Energy billed in this period |
| `energy_consumed_kwh` | kWh | actual | Raw meter consumption in this period |
| `actual_total_billed` | NGN | actual | Revenue including VAT |
| `energy_charge` | NGN | actual | Revenue excluding VAT |
| `vat` | NGN | actual | VAT component |
| `customers_read` | — | actual | Customers read in this period |
| `coverage_rate` | % | actual | Coverage in this period |
| `arpu` | NGN | actual | ARPU in this period |

---

## Quick Reference — All Endpoints

```
GET /api/commercial/overview/                          Full system KPIs
GET /api/commercial/trend/                             Current + last 4 periods (8 KPIs)
GET /api/commercial/trend/?state=KN                   Trend scoped to a state
GET /api/commercial/states/                            All states
GET /api/commercial/states/<slug>/                     Single state
GET /api/commercial/districts/                         All districts
GET /api/commercial/districts/?state=<slug>            Districts in a state
GET /api/commercial/districts/<slug>/                  Single district
GET /api/commercial/feeders/                           All feeders
GET /api/commercial/feeders/?state=<slug>              Feeders in a state
GET /api/commercial/feeders/?district=<slug>           Feeders in a district
GET /api/commercial/feeders/<slug>/                    Single feeder
GET /api/commercial/bands/                             All service bands (A–E)
GET /api/commercial/bands/<slug>/                      Single band
GET /api/commercial/customers/                         Paginated customer list
GET /api/commercial/customers/?search=<query>          Customer search
GET /api/commercial/customers/?feeder=<slug>           Customers on a feeder (drill-down)
GET /api/commercial/customers/?state=<slug>            Customers in a state
GET /api/commercial/customers/?district=<slug>         Customers in a district
GET /api/commercial/customers/top/                     Top 10 customers by billing
GET /api/commercial/customers/top/?n=50                Top N (max 50)
GET /api/commercial/customers/top/?order=bottom        Bottom 10 customers by billing
GET /api/commercial/customers/top/?order=bottom&n=50   Bottom N (max 50)
GET /api/commercial/customers/<uuid>/                  Customer detail + readings
```

---

*KEDCO Raven Commercial Analytics Module — staging environment.*
