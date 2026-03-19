# KEDCO Raven — Commercial Analytics API
### Frontend Integration Guide

**Base URL (staging):** `https://staging.yourdomain.com/api/commercial/`
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
9. [Data Modes — actual vs estimated](#9-data-modes--actual-vs-estimated)
10. [KPI Reference](#10-kpi-reference)

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

### Time Mode Examples
```
?mode=monthly&year=2026&month=3          → March 2026
?mode=daily&from_date=2026-03-15         → March 15 2026
?mode=weekly&from_date=2026-03-10        → Week starting March 10
?mode=yearly&year=2026                   → Full year 2026
?mode=monthly&year=2026&month=3&type=MDI → March 2026, MDI customers only
```

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
| `mode` | `"actual"` or `"estimated"` | Whether this is real data or a projection |
| `explanation` | `string` | Always present — human-readable description for tooltips |

> **UI Tip:** Use `mode === "estimated"` to visually flag values (e.g. italic, dashed border, ≈ prefix). Never hide the `explanation` — show it as a tooltip on every KPI card.

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
    "start_date": "2026-03-01",
    "end_date": "2026-03-19",
    "label": "March 2026",
    "days": 19
  },
  "customers": {
    "total":  { "value": 1871, "unit": "", "mode": "actual", "explanation": "..." },
    "mdi":    { "value": 1157, "unit": "", "mode": "actual", "explanation": "..." },
    "mdni":   { "value": 714,  "unit": "", "mode": "actual", "explanation": "..." }
  },
  "energy": {
    "actual_billed_kwh":          { "value": 50.0,           "unit": "kWh",     "mode": "actual",     "explanation": "..." },
    "estimated_billed_kwh":       { "value": 118375736.21,   "unit": "kWh",     "mode": "estimated",  "explanation": "..." },
    "total_projected_billed_kwh": { "value": 118375786.21,   "unit": "kWh",     "mode": "estimated",  "explanation": "..." },
    "daily_billed_kwh_estimate":  { "value": 2.6316,         "unit": "kWh/day", "mode": "estimated",  "explanation": "..." },
    "daily_energy_delivered_mwh": { "value": 24400.2391,     "unit": "MWh/day", "mode": "estimated",  "explanation": "..." },
    "energy_delivered_vs_billed": {
      "value": {
        "delivered_kwh":        463604542.9,
        "actual_billed_kwh":    50.0,
        "projected_billed_kwh": 118375786.21,
        "gap_kwh":              463604492.9
      },
      "unit": "kWh", "mode": "estimated", "explanation": "..."
    }
  },
  "revenue": {
    "actual_energy_charge":    { "value": 10475.0,           "unit": "NGN", "mode": "actual",    "explanation": "..." },
    "estimated_energy_charge": { "value": 24444869242.19,    "unit": "NGN", "mode": "estimated", "explanation": "..." },
    "actual_vat":              { "value": 785.62,            "unit": "NGN", "mode": "actual",    "explanation": "..." },
    "actual_total_billed":     { "value": 11260.62,          "unit": "NGN", "mode": "actual",    "explanation": "..." },
    "estimated_revenue":       { "value": 26278234435.36,    "unit": "NGN", "mode": "estimated", "explanation": "..." },
    "total_projected_revenue": { "value": 26278245695.98,    "unit": "NGN", "mode": "estimated", "explanation": "..." },
    "mdi_revenue_split":       { "value": 0.0,               "unit": "%",   "mode": "actual",    "explanation": "..." },
    "mdni_revenue_split":      { "value": 100.0,             "unit": "%",   "mode": "actual",    "explanation": "..." },
    "arpu":                    { "value": 11260.62,           "unit": "NGN", "mode": "actual",    "explanation": "..." }
  },
  "performance": {
    "coverage_rate":      { "value": 0.37,  "unit": "%", "mode": "actual",    "explanation": "..." },
    "customers_read":     { "value": 7,     "unit": "",  "mode": "actual",    "explanation": "..." },
    "unread_customers":   { "value": 1864,  "unit": "",  "mode": "actual",    "explanation": "..." },
    "billing_efficiency": { "value": 0.0,   "unit": "%", "mode": "estimated", "explanation": "..." },
    "atc_loss":           { "value": 100.0, "unit": "%", "mode": "estimated", "explanation": "..." }
  },
  "managers": {
    "total_mdi_managers":  { "value": 45, "unit": "", "mode": "actual", "explanation": "..." },
    "total_mdni_managers": { "value": 38, "unit": "", "mode": "actual", "explanation": "..." }
  }
}
```

---

## 4. States

### List all states
```
GET /api/commercial/states/
```

### Single state
```
GET /api/commercial/states/<slug>/
```
**Slug examples:** `KN` `JG` `KT`

### Response (list)
```json
{
  "period": { ... },
  "count": 3,
  "states": [
    {
      "state": { "slug": "JG", "name": "Jigawa" },
      "customers":   { "total": {...}, "mdi": {...}, "mdni": {...} },
      "energy":      { "actual_billed_kwh": {...}, "estimated_billed_kwh": {...}, ... },
      "revenue":     { "actual_total_billed": {...}, "total_projected_revenue": {...}, ... },
      "performance": { "coverage_rate": {...}, "atc_loss": {...}, ... },
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
GET /api/commercial/districts/?state=KN          ← scope to one state
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
GET /api/commercial/feeders/?state=KN              ← scope to state
GET /api/commercial/feeders/?district=KN-IDU       ← scope to district
GET /api/commercial/feeders/?state=KN&type=MDI     ← combine filters
```

### Single feeder
```
GET /api/commercial/feeders/<slug>/
```
**Slug examples:** `KN-TAM-COC` `KN-NW-ABR`

### Response (list)
```json
{
  "period": { ... },
  "count": 221,
  "feeders": [
    {
      "feeder": {
        "slug": "KN-TAM-COC",
        "name": "COCA COLA",
        "voltage_level": "11kv",
        "feeder_class": "MDI",
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

#### Extra filters (on top of global params)
| Parameter | Description |
|---|---|
| `search` | Search by customer name, account number, or meter number |
| `feeder` | Feeder slug — e.g. `KN-TAM-COC` |
| `district` | District slug — e.g. `KN-IDU` |
| `state` | State slug — e.g. `KN` |
| `page` | Page number (default `1`) |
| `page_size` | Results per page (default `50`, max `200`) |

#### Example requests
```
GET /api/commercial/customers/?search=dangote
GET /api/commercial/customers/?type=MDI&state=KN&page=2
GET /api/commercial/customers/?district=KN-IDU&page_size=100
GET /api/commercial/customers/?feeder=KN-TAM-COC&mode=monthly&year=2026&month=3
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
      "external_id":      "32214922-596f-47f8-b40b-e9b98f8fe6c2",
      "account_no":       "32/25/90/0517-01",
      "meter_number":     "252424078",
      "customer_name":    "(FATA TANNING LIMITED) WEST AFRICAN TANNERY",
      "customer_type":    "MDI",
      "customer_address": "PLOT 53 CHALLAWA INDUSTRIAL ESTATE KANO",
      "phone_number":     "8028647304",
      "feeder":   { "slug": "KN-TAM-COC", "name": "COCA COLA" },
      "district": { "slug": "KN-IDU",     "name": "Kano Industrial" },
      "state":    { "slug": "KN",          "name": "Kano" },
      "period_billing": {
        "readings_count":   0,
        "total_billed_kwh": 0.0,
        "energy_charge":    0.0,
        "vat":              0.0,
        "total_billed":     0.0,
        "last_reading_date": null
      }
    },
    ...
  ]
}
```

---

### Customer detail
```
GET /api/commercial/customers/<id>/
```
`<id>` is the UUID from the list response (`id` field).

#### Response
```json
{
  "period": { ... },
  "customer": {
    "id":               "1011e552-e6b0-4c46-b342-4ea0abaa9b3f",
    "external_id":      "32214922-596f-47f8-b40b-e9b98f8fe6c2",
    "account_no":       "32/25/90/0517-01",
    "meter_number":     "252424078",
    "customer_name":    "(FATA TANNING LIMITED) WEST AFRICAN TANNERY",
    "customer_type":    "MDI",
    "customer_address": "PLOT 53 CHALLAWA INDUSTRIAL ESTATE KANO",
    "phone_number":     "8028647304",
    "feeder":   { "slug": "KN-TAM-COC", "name": "COCA COLA" },
    "district": { "slug": "KN-IDU",     "name": "Kano Industrial" },
    "state":    { "slug": "KN",          "name": "Kano" }
  },
  "period_billing": {
    "readings_count":    2,
    "total_billed_kwh":  150.0,
    "energy_charge":     31425.0,
    "vat":               2356.88,
    "total_billed":      33781.88,
    "last_reading_date": "2026-03-14"
  },
  "readings": [
    {
      "id":                 "uuid",
      "reading_date":       "2026-03-14",
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

### Top N customers
```
GET /api/commercial/customers/top/
GET /api/commercial/customers/top/?n=20
GET /api/commercial/customers/top/?n=10&state=KN&type=MDI
```

| Parameter | Default | Max |
|---|---|---|
| `n` | `10` | `50` |

#### Response
```json
{
  "period": { ... },
  "n": 10,
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
        "readings_count":   3,
        "total_billed_kwh": 450.0,
        "energy_charge":    94275.0,
        "vat":              7070.63,
        "total_billed":     101345.63,
        "last_reading_date": "2026-03-18"
      }
    },
    ...
  ]
}
```
> **Note:** Results are sorted by `period_billing.total_billed` descending.

---

## 9. Data Modes — actual vs estimated

Every metric carries a `mode` field. Here is what it means:

| Mode | Meaning | UI Treatment |
|---|---|---|
| `"actual"` | Computed from real meter readings submitted in the period | Show as solid/confirmed value |
| `"estimated"` | Projected using last known daily average for unread customers | Show with ≈ prefix, dashed border, or italic — make it clear this is not real data |

### Estimation logic (for transparency in UI)
- **Who gets estimated?** Customers with zero readings in the selected period.
- **How?** Their last submitted reading's `billed_consumption ÷ 7` gives a daily average. That is multiplied by the number of days in the period.
- **Energy delivered** is always estimated — it's the 90-day average from technical feeder readings.

---

## 10. KPI Reference

Quick lookup for every KPI returned across all endpoints.

### customers
| Key | Unit | Mode | Description |
|---|---|---|---|
| `total` | — | actual | Total MDI + MDNI customers |
| `mdi` | — | actual | Maximum Demand Installation customers |
| `mdni` | — | actual | Non Maximum Demand customers |

### energy
| Key | Unit | Mode | Description |
|---|---|---|---|
| `actual_billed_kwh` | kWh | actual | Energy from real readings |
| `estimated_billed_kwh` | kWh | estimated | Projected energy for unread customers |
| `total_projected_billed_kwh` | kWh | estimated | actual + estimated |
| `daily_billed_kwh_estimate` | kWh/day | estimated | Actual billed ÷ days in period |
| `daily_energy_delivered_mwh` | MWh/day | estimated | 90-day feeder average |
| `energy_delivered_vs_billed` | kWh | estimated | Object with `delivered_kwh`, `actual_billed_kwh`, `projected_billed_kwh`, `gap_kwh` |

### revenue
| Key | Unit | Mode | Description |
|---|---|---|---|
| `actual_energy_charge` | NGN | actual | Energy charge from real readings (excl VAT) |
| `estimated_energy_charge` | NGN | estimated | Projected energy charge for unread customers |
| `actual_vat` | NGN | actual | 7.5% VAT on actual energy charge |
| `actual_total_billed` | NGN | actual | Total billed = energy_charge + VAT |
| `estimated_revenue` | NGN | estimated | Revenue at risk from unread customers (incl VAT) |
| `total_projected_revenue` | NGN | estimated | actual_total_billed + estimated_revenue |
| `mdi_revenue_split` | % | actual | % of actual revenue from MDI customers |
| `mdni_revenue_split` | % | actual | % of actual revenue from MDNI customers |
| `arpu` | NGN | actual | Actual total billed ÷ customers read |

### performance
| Key | Unit | Mode | Description |
|---|---|---|---|
| `coverage_rate` | % | actual | % of customers with a reading in this period |
| `customers_read` | — | actual | Count of customers with at least one reading |
| `unread_customers` | — | actual | Customers with no reading — revenue at risk |
| `billing_efficiency` | % | estimated | Energy billed ÷ energy delivered × 100 |
| `atc_loss` | % | estimated | 100 − billing_efficiency (AT&C loss) |

### managers
| Key | Unit | Mode | Description |
|---|---|---|---|
| `total_mdi_managers` | — | actual | MDI field officers with active assignments |
| `total_mdni_managers` | — | actual | MDNI field officers with active assignments |

---

## Quick Reference — All Endpoints

```
GET /api/commercial/overview/                          Full system KPIs
GET /api/commercial/states/                            All states
GET /api/commercial/states/<slug>/                     Single state
GET /api/commercial/districts/                         All districts
GET /api/commercial/districts/?state=<slug>            Districts in a state
GET /api/commercial/districts/<slug>/                  Single district
GET /api/commercial/feeders/                           All feeders
GET /api/commercial/feeders/?state=<slug>              Feeders in a state
GET /api/commercial/feeders/?district=<slug>           Feeders in a district
GET /api/commercial/feeders/<slug>/                    Single feeder
GET /api/commercial/bands/                             All bands (A–E)
GET /api/commercial/bands/<slug>/                      Single band
GET /api/commercial/customers/                         Paginated customer list
GET /api/commercial/customers/?search=<query>          Customer search
GET /api/commercial/customers/?state=<slug>            Customers in state
GET /api/commercial/customers/?district=<slug>         Customers in district
GET /api/commercial/customers/?feeder=<slug>           Customers on feeder
GET /api/commercial/customers/top/                     Top 10 customers by billing
GET /api/commercial/customers/top/?n=25                Top N (max 50)
GET /api/commercial/customers/<uuid>/                  Customer detail + readings
```

---

*Generated for KEDCO Raven Commercial Analytics Module — staging environment.*
