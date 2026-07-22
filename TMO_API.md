# TMO Technical Dashboard API

> This API powers the **TMO Technical Dashboard** — the daily operational view used by the Technical Management Operations team to monitor feeder energy dispatch, hours of supply compliance, billing efficiency, and collection performance across all KEDCO feeders.

Base URL: `/api/tmo/`

All endpoints require a valid JWT token:
```
Authorization: Bearer <token>
```

---

## Date Parameters

Every endpoint accepts the same date params. **Default is T-1 (yesterday)** if nothing is passed.

| Param | Format | Example | Description |
|---|---|---|---|
| `date` | `YYYY-MM-DD` | `?date=2026-07-21` | Single day view |
| `month` | `YYYY-MM` | `?month=2026-07` | Full month view |
| `from_date` + `to_date` | `YYYY-MM-DD` | `?from_date=2026-07-01&to_date=2026-07-21` | Custom range |

---

## Filter Parameters

All list endpoints also accept these filters (combinable):

| Param | Example | Description |
|---|---|---|
| `segment` | `?segment=MDI` | Filter by P&L segment: `MDI`, `MDNI`, or `MINIGRID` |
| `state` | `?state=kano` | Filter by state slug |
| `district` | `?district=kano-industrial` | Filter by business district slug |
| `band` | `?band=a` | Filter by band slug (`a`, `b`, `c`, `d`, `e`) |
| `voltage` | `?voltage=11kv` | Filter by voltage level: `11kv` or `33kv` |
| `feeder` | `?feeder=KN-ABA-HAS` | Filter to a single feeder slug |

---

## Status Values

All `status` fields return one of:

| Value | Meaning |
|---|---|
| `on_target` | ≥ 100% achievement |
| `below_target` | 90–99% |
| `poor` | 75–89% |
| `critical` | < 75% |

---

## Endpoints

### 1. Overview
`GET /api/tmo/overview/`

Dashboard header KPIs — energy dispatch achievement and supply compliance summary.

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "total_feeders": 210,
  "energy_dispatch": {
    "target_mwh": 4520.00,
    "actual_mwh": 3980.50,
    "variance_mwh": -539.50,
    "achievement_pct": 88.1,
    "status": "poor"
  },
  "supply_compliance": {
    "compliant_feeders": 142,
    "total_feeders": 210,
    "compliance_pct": 67.6
  }
}
```

---

### 2. Feeder Dispatch
`GET /api/tmo/energy/dispatch/`

Per-feeder energy: target MWh vs actual MWh. Sorted by `achievement_pct` ascending (worst feeders first).

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "feeders": [
    {
      "feeder_id": "uuid",
      "feeder_name": "HASKE SOLAR",
      "feeder_slug": "KN-ABA-HAS",
      "segment": "MDI",
      "band": "A",
      "state": "Kano",
      "district": "Kano Industrial",
      "is_minigrid": true,
      "target_mwh": 12.0000,
      "actual_mwh": 10.5000,
      "variance_mwh": -1.5000,
      "variance_pct": -12.5,
      "achievement_pct": 87.5,
      "status": "poor"
    }
  ],
  "summary": {
    "total_target_mwh": 4520.00,
    "total_actual_mwh": 3980.50,
    "variance_mwh": -539.50,
    "overall_achievement_pct": 88.1,
    "overall_status": "poor"
  }
}
```

---

### 3. Energy by Segment
`GET /api/tmo/energy/by-segment/`

Energy totals grouped by P&L segment: **MDI**, **MDNI**, **Minigrid**.

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "segments": [
    {
      "segment": "MDI",
      "feeder_count": 18,
      "target_mwh": 850.00,
      "actual_mwh": 790.00,
      "variance_mwh": -60.00,
      "achievement_pct": 92.9,
      "status": "below_target"
    },
    {
      "segment": "MDNI",
      "feeder_count": 12,
      "target_mwh": 620.00,
      "actual_mwh": 650.00,
      "variance_mwh": 30.00,
      "achievement_pct": 104.8,
      "status": "on_target"
    },
    {
      "segment": "Minigrid",
      "feeder_count": 1,
      "target_mwh": 12.00,
      "actual_mwh": 10.50,
      "variance_mwh": -1.50,
      "achievement_pct": 87.5,
      "status": "poor"
    }
  ]
}
```

---

### 4. Supply Compliance
`GET /api/tmo/supply/compliance/`

Per-feeder hours of supply vs NERC Band minimum hours. Sorted worst first.

| Band | NERC Minimum |
|---|---|
| A | 20 hrs/day |
| B | 16 hrs/day |
| C | 12 hrs/day |
| D | 8 hrs/day |
| E | 4 hrs/day |

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "feeders": [
    {
      "feeder_id": "uuid",
      "feeder_name": "HASKE SOLAR",
      "segment": "MDI",
      "band": "A",
      "band_minimum_hours": 20.0,
      "avg_daily_hours": 18.5,
      "total_hours": 18.5,
      "days_recorded": 1,
      "compliance_pct": 92.5,
      "status": "below_target",
      "is_minigrid": true
    }
  ],
  "summary": {
    "compliant_feeders": 142,
    "total_feeders": 210,
    "compliance_rate_pct": 67.6
  }
}
```

---

### 5. Collection Performance
`GET /api/tmo/collection/`

Collection target vs actual by segment and period.

**Additional filter:** `?segment=MDI` or `?segment=MDNI`

**Response**
```json
{
  "period": { "from": "2026-07-01", "to": "2026-07-21" },
  "rows": [
    {
      "segment_code": "MDI",
      "sub_segment": "LARGE",
      "period_month": "2026-07-01",
      "target_amount": 50000000.00,
      "actual_amount": 46500000.00,
      "variance": -3500000.00,
      "achievement_pct": 93.0,
      "status": "below_target"
    }
  ],
  "summary": {
    "total_target": 120000000.00,
    "total_actual": 110000000.00,
    "variance": -10000000.00,
    "overall_achievement_pct": 91.7,
    "overall_status": "below_target"
  }
}
```

---

### 6. Billing Efficiency
`GET /api/tmo/billing/`

Billing efficiency % (energy billed / energy delivered) and revenue realisation % (billed ₦ / target ₦).

**Response**
```json
{
  "period": { "from": "2026-07-01", "to": "2026-07-21" },
  "rows": [
    {
      "scope_type": "segment",
      "scope_code": "MDI",
      "scope_label": "MD Industrial",
      "period_month": "2026-07-01",
      "energy_delivered_gwh": 0.85,
      "energy_billed_gwh": 0.80,
      "billing_efficiency_pct": 94.1,
      "target_revenue": 85000000.00,
      "billed_amount": 79000000.00,
      "revenue_efficiency_pct": 92.9,
      "be_status": "below_target",
      "rr_status": "below_target"
    }
  ],
  "summary": {
    "total_energy_delivered_gwh": 4.520,
    "total_energy_billed_gwh": 4.100,
    "overall_billing_eff_pct": 90.7,
    "total_target_revenue": 450000000.00,
    "total_billed_amount": 410000000.00,
    "overall_revenue_eff_pct": 91.1
  }
}
```

---

### 7. P&L Segment Targets
`GET /api/tmo/pnl/`

MDI and MDNI energy actuals vs monthly targets set by management. Also returns revenue and collection targets for the month.

> Uses the month of `from_date` (or current month if default T-1) to look up targets.

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "segments": [
    {
      "segment": "MDI",
      "feeder_count": 18,
      "target_energy_mwh": 850.00,
      "actual_energy_mwh": 790.00,
      "energy_achievement_pct": 92.9,
      "energy_status": "below_target",
      "target_revenue_ngn": 85000000.00,
      "target_collection_ngn": 75000000.00
    },
    {
      "segment": "MDNI",
      "feeder_count": 12,
      "target_energy_mwh": 620.00,
      "actual_energy_mwh": 650.00,
      "energy_achievement_pct": 104.8,
      "energy_status": "on_target",
      "target_revenue_ngn": 62000000.00,
      "target_collection_ngn": 55000000.00
    }
  ]
}
```

> **Note:** `target_revenue_ngn` and `target_collection_ngn` will be `0.00` until management seeds the `TMOMonthlySegmentTarget` table via Django admin.

---

### 8. Minigrids
`GET /api/tmo/minigrids/`

Energy dispatch and hours of supply for minigrid feeders only (e.g. Haske Solar).

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "count": 1,
  "minigrids": [
    {
      "feeder_id": "uuid",
      "feeder_name": "HASKE SOLAR",
      "state": "Kano",
      "target_mwh": 12.0000,
      "actual_mwh": 10.5000,
      "variance_mwh": -1.5000,
      "achievement_pct": 87.5,
      "avg_daily_hours": 18.5,
      "status": "poor"
    }
  ]
}
```

---

### 9. All Feeders
`GET /api/tmo/feeders/`

Full feeder list with energy and hours side-by-side. Sorted by `energy_achievement_pct` ascending (worst first). Supports all filters.

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "count": 210,
  "feeders": [
    {
      "feeder_id": "uuid",
      "feeder_name": "HASKE SOLAR",
      "feeder_slug": "KN-ABA-HAS",
      "segment": "MDI",
      "band": "A",
      "voltage_level": "33kv",
      "state": "Kano",
      "district": "Kano Industrial",
      "is_minigrid": true,
      "target_mwh": 12.0000,
      "actual_mwh": 10.5000,
      "variance_mwh": -1.5000,
      "energy_achievement_pct": 87.5,
      "energy_status": "poor",
      "avg_daily_hours": 18.5,
      "band_minimum_hours": 20.0,
      "hours_compliance_pct": 92.5,
      "hours_status": "below_target"
    }
  ]
}
```

---

### 10. Single Feeder Detail
`GET /api/tmo/feeders/<feeder_slug>/`

Daily breakdown for one feeder over the selected period. Use this for the drill-down view.

**Example:** `GET /api/tmo/feeders/KN-ABA-HAS/?month=2026-07`

**Response**
```json
{
  "feeder": {
    "id": "uuid",
    "name": "HASKE SOLAR",
    "slug": "KN-ABA-HAS",
    "segment": "MDI",
    "band": "A",
    "voltage_level": "33kv",
    "state": "Kano",
    "district": "Kano Industrial",
    "is_minigrid": true
  },
  "period": { "from": "2026-07-01", "to": "2026-07-21" },
  "days": [
    {
      "date": "2026-07-01",
      "target_mwh": 12.0000,
      "actual_mwh": 11.2000,
      "variance_mwh": -0.8000,
      "achievement_pct": 93.3,
      "hours_supplied": 19.5,
      "status": "below_target"
    }
  ],
  "summary": {
    "total_target_mwh": 240.00,
    "total_actual_mwh": 220.50,
    "variance_mwh": -19.50,
    "overall_achievement_pct": 91.9,
    "overall_status": "below_target"
  }
}
```

---

## Quick Reference

| Endpoint | URL |
|---|---|
| Overview | `GET /api/tmo/overview/` |
| Feeder Dispatch | `GET /api/tmo/energy/dispatch/` |
| Energy by Segment | `GET /api/tmo/energy/by-segment/` |
| Supply Compliance | `GET /api/tmo/supply/compliance/` |
| Collection | `GET /api/tmo/collection/` |
| Billing Efficiency | `GET /api/tmo/billing/` |
| P&L Targets | `GET /api/tmo/pnl/` |
| Minigrids | `GET /api/tmo/minigrids/` |
| All Feeders | `GET /api/tmo/feeders/` |
| Feeder Detail | `GET /api/tmo/feeders/<feeder_slug>/` |
