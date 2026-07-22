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

### 11. P&L Mix Volatility Index
`GET /api/tmo/volatility/`

Compares each segment's **share of total energy** for the selected day vs month-to-date.
Flags when high-ROI segments (MDI/MDNI) are declining vs the MTD average.

> Default: T-1 day vs start-of-current-month → T-1.

**Response**
```json
{
  "day": "2026-07-21",
  "mtd_from": "2026-07-01",
  "day_total_mwh": 4200.50,
  "mtd_total_mwh": 88200.00,
  "segments": [
    {
      "segment": "MDI",
      "yesterday_share_pct": 37.4,
      "mtd_share_pct": 40.6,
      "difference_pct": -3.3,
      "remark": "Decline"
    },
    {
      "segment": "MDNI",
      "yesterday_share_pct": 13.8,
      "mtd_share_pct": 15.0,
      "difference_pct": -1.3,
      "remark": "Decline"
    },
    {
      "segment": "Regions",
      "yesterday_share_pct": 48.9,
      "mtd_share_pct": 44.4,
      "difference_pct": 4.5,
      "remark": "High — Daily spike sustained"
    },
    {
      "segment": "Minigrid",
      "yesterday_share_pct": 0.2,
      "mtd_share_pct": 0.2,
      "difference_pct": 0.0,
      "remark": "Stable"
    }
  ]
}
```

**Remark logic:**

| Segment | Condition | Remark |
|---|---|---|
| MDI / MDNI | difference < -1% | `Decline` |
| MDI / MDNI | difference > +1% | `Growth` |
| MDI / MDNI | within ±1% | `Stable` |
| Regions / Minigrid | difference > +1% | `High — Daily spike sustained` |
| Regions / Minigrid | difference < -1% | `Declining` |
| Regions / Minigrid | within ±1% | `Stable` |

---

### 12. Daily Network Energy
`GET /api/tmo/energy/daily/`

Daily total network energy (GWh) per day for the selected period, compared against the daily target derived from the monthly GWh target in **TMONetworkConfig**.  
Also returns MTD cumulative vs monthly target. Covers **Slides 2 & 3** (Daily Energy Forecast / Daily Energy Allocation).

> Default: yesterday only. Pass `?month=2026-07` to see the full month's daily chart.

> **Note:** `monthly_target_gwh` will be `0.0` until TMONetworkConfig is seeded via Django admin.

**Response**
```json
{
  "period": { "from": "2026-07-01", "to": "2026-07-21" },
  "monthly_target_gwh": 150.0000,
  "total_actual_gwh": 108.4200,
  "mtd_achievement_pct": 72.3,
  "mtd_status": "critical",
  "days": [
    {
      "date": "2026-07-01",
      "day": 1,
      "target_gwh": 4.8387,
      "actual_gwh": 5.2100,
      "variance_gwh": 0.3713,
      "achievement_pct": 107.7,
      "status": "on_target"
    },
    {
      "date": "2026-07-02",
      "day": 2,
      "target_gwh": 4.8387,
      "actual_gwh": 4.1200,
      "variance_gwh": -0.7187,
      "achievement_pct": 85.1,
      "status": "poor"
    }
  ]
}
```

---

### 13. PEAR — Premium Energy Allocation Ratio
`GET /api/tmo/pear/`

Compares the **MD (MDI + MDNI) share** vs **NMD share** of total energy for yesterday and MTD, against the configured target MD/NMD mix (default **65% / 35%**). Covers **Slide 10**.

> Target mix is set per month in **TMONetworkConfig** (`target_md_share_pct`). Default is 65.0 if not configured.

**Response**
```json
{
  "day": "2026-07-21",
  "mtd_from": "2026-07-01",
  "target_mix": {
    "md_pct": 65.0,
    "nmd_pct": 35.0
  },
  "yesterday": {
    "total_mwh": 4200.50,
    "md_mwh": 2520.30,
    "nmd_mwh": 1680.20,
    "md_share_pct": 60.0,
    "nmd_share_pct": 40.0
  },
  "mtd": {
    "total_mwh": 88200.00,
    "md_mwh": 55125.00,
    "nmd_mwh": 33075.00,
    "md_share_pct": 62.5,
    "nmd_share_pct": 37.5
  }
}
```

**How to read it:** If `yesterday.md_share_pct` is significantly below `target_mix.md_pct`, the network sent less energy to premium segments than planned — revenue impact is immediate.

---

### 14. Compliance Summary by Segment
`GET /api/tmo/supply/compliance/summary/`

Feeder counts bucketed by compliance status across three segments: **MDI**, **Non-MDI Band A**, and **Non-MDI Non-Band A**. Covers **Slide 6**.

**Compliance buckets:**

| Bucket | Hours vs Target |
|---|---|
| `exceeding` | > 105% |
| `on_target` | 95 – 105% |
| `below_target` | 85 – 94% |
| `poor` | 75 – 84% |
| `critical` | < 75% |

> **Note:** Compliance is measured against feeder **targets** (from `TMOFeederTarget`), not NERC Band minimums. All counts will be `0` in each bucket until `TMOFeederTarget` is populated.

**Response**
```json
{
  "period": { "from": "2026-07-21", "to": "2026-07-21" },
  "segments": [
    {
      "segment": "MDI",
      "total_feeders": 18,
      "buckets": {
        "exceeding":    { "count": 3, "pct": 16.7 },
        "on_target":    { "count": 8, "pct": 44.4 },
        "below_target": { "count": 4, "pct": 22.2 },
        "poor":         { "count": 2, "pct": 11.1 },
        "critical":     { "count": 1, "pct": 5.6 }
      }
    },
    {
      "segment": "Non-MDI Band A",
      "total_feeders": 45,
      "buckets": {
        "exceeding":    { "count": 5, "pct": 11.1 },
        "on_target":    { "count": 20, "pct": 44.4 },
        "below_target": { "count": 12, "pct": 26.7 },
        "poor":         { "count": 5, "pct": 11.1 },
        "critical":     { "count": 3, "pct": 6.7 }
      }
    },
    {
      "segment": "Non-MDI, Non-Band A",
      "total_feeders": 147,
      "buckets": {
        "exceeding":    { "count": 10, "pct": 6.8 },
        "on_target":    { "count": 60, "pct": 40.8 },
        "below_target": { "count": 40, "pct": 27.2 },
        "poor":         { "count": 25, "pct": 17.0 },
        "critical":     { "count": 12, "pct": 8.2 }
      }
    }
  ]
}
```

---

### 15. Energy by Voltage
`GET /api/tmo/energy/by-voltage/`

Per-segment energy split by **33KV vs 11KV** feeders for each day in the period, plus a **current month vs previous month** total comparison. Covers **Slides 13, 14, 15**.

> Segments in this view: `MDI`, `MDNI`, `Regional` (all non-MDI/MDNI feeders).

**Response**
```json
{
  "period": { "from": "2026-07-01", "to": "2026-07-21" },
  "days": [
    {
      "date": "2026-07-01",
      "segments": {
        "MDI": {
          "energy_33kv_mwh": 520.4000,
          "energy_11kv_mwh": 180.6000,
          "total_mwh": 701.0000
        },
        "MDNI": {
          "energy_33kv_mwh": 0.0000,
          "energy_11kv_mwh": 310.2000,
          "total_mwh": 310.2000
        },
        "Regional": {
          "energy_33kv_mwh": 1200.0000,
          "energy_11kv_mwh": 900.0000,
          "total_mwh": 2100.0000
        }
      }
    }
  ],
  "month_comparison": {
    "MDI": {
      "current_month": {
        "energy_33kv_mwh": 10920.84,
        "energy_11kv_mwh": 3781.26,
        "total_mwh": 14702.10
      },
      "previous_month": {
        "energy_33kv_mwh": 11450.00,
        "energy_11kv_mwh": 3900.00,
        "total_mwh": 15350.00
      }
    },
    "MDNI": {
      "current_month": {
        "energy_33kv_mwh": 0.00,
        "energy_11kv_mwh": 6510.42,
        "total_mwh": 6510.42
      },
      "previous_month": {
        "energy_33kv_mwh": 0.00,
        "energy_11kv_mwh": 6800.00,
        "total_mwh": 6800.00
      }
    },
    "Regional": {
      "current_month": {
        "energy_33kv_mwh": 25200.00,
        "energy_11kv_mwh": 18900.00,
        "total_mwh": 44100.00
      },
      "previous_month": {
        "energy_33kv_mwh": 26000.00,
        "energy_11kv_mwh": 19500.00,
        "total_mwh": 45500.00
      }
    }
  }
}
```

---

### 16. Techno-Commercial Incidents
`GET /api/tmo/incidents/`

Techno-commercial fault report per feeder — nature of fault, financial loss, and rectification status. Covers **Slide 16**.

> Incidents are entered manually via Django admin (`TMOIncident`). By default, returns all incidents where `incident_date` falls within the selected period.

**Additional filters:** `?state=`, `?district=`, `?feeder=`

**Response**
```json
{
  "period": { "from": "2026-07-01", "to": "2026-07-21" },
  "summary": {
    "total_incidents": 12,
    "rectified": 8,
    "lingering": 4,
    "rectification_rate_pct": 66.7,
    "total_financial_loss_ngn": 45000000.00
  },
  "incidents": [
    {
      "id": 1,
      "feeder_name": "CHALLAWA FEEDER",
      "feeder_slug": "KN-CHL-001",
      "coordinate": "KANO",
      "region": "KANO SOUTH",
      "nature_of_fault": "Transformer blown — 500KVA unit on TS 4",
      "status": "lingering",
      "financial_loss_ngn": 8500000.00,
      "incident_date": "2026-07-05",
      "rectified_date": null
    },
    {
      "id": 2,
      "feeder_name": "PANSHEKARA FEEDER",
      "feeder_slug": "KN-PAN-001",
      "coordinate": "KANO",
      "region": "KANO NORTH",
      "nature_of_fault": "Underground cable fault — 11KV section",
      "status": "rectified",
      "financial_loss_ngn": 2200000.00,
      "incident_date": "2026-07-08",
      "rectified_date": "2026-07-10"
    }
  ]
}
```

**Status values for incidents:**

| Value | Meaning |
|---|---|
| `rectified` | Fault has been fixed |
| `lingering` | Fault is still active / unresolved |

---

### 17. GCR — Energy Gap-to-Cost Ratio
`GET /api/tmo/gcr/`

P&L target vs billing value realization per segment. Converts energy gap (target − consumed GWh) into a naira billing value using the average tariff per segment. Covers **Slide 18**.

> Tariff and targets are set per segment per month in **TMOMonthlySegmentTarget** (`target_energy_mwh` and `average_tariff_ngn_per_mwh`). All values will be `0.00` until these are seeded via Django admin.

**Response**
```json
{
  "period": { "from": "2026-07-01", "to": "2026-07-21" },
  "rows": [
    {
      "segment": "MDI",
      "target_gwh": 18.5000,
      "consumed_gwh": 14.7020,
      "gap_gwh": 3.7980,
      "expected_bill_value": 296000000.00,
      "mtd_bill_value": 235232000.00,
      "gap_bill_value": 60768000.00,
      "mtd_achievement_pct": 79.5,
      "gap_pct": 20.5,
      "average_tariff_ngn_per_mwh": 16000.00
    },
    {
      "segment": "MDNI",
      "target_gwh": 12.0000,
      "consumed_gwh": 6.5100,
      "gap_gwh": 5.4900,
      "expected_bill_value": 156000000.00,
      "mtd_bill_value": 84630000.00,
      "gap_bill_value": 71370000.00,
      "mtd_achievement_pct": 54.3,
      "gap_pct": 45.7,
      "average_tariff_ngn_per_mwh": 13000.00
    },
    {
      "segment": "Regions",
      "target_gwh": 119.5000,
      "consumed_gwh": 87.2080,
      "gap_gwh": 32.2920,
      "expected_bill_value": 956000000.00,
      "mtd_bill_value": 697664000.00,
      "gap_bill_value": 258336000.00,
      "mtd_achievement_pct": 73.0,
      "gap_pct": 27.0,
      "average_tariff_ngn_per_mwh": 8000.00
    },
    {
      "segment": "Total",
      "target_gwh": 150.0000,
      "consumed_gwh": 108.4200,
      "gap_gwh": 41.5800,
      "expected_bill_value": 1408000000.00,
      "mtd_bill_value": 1017526000.00,
      "gap_bill_value": 390474000.00,
      "mtd_achievement_pct": 72.3,
      "gap_pct": 27.7,
      "average_tariff_ngn_per_mwh": null
    }
  ]
}
```

**Field glossary:**

| Field | Description |
|---|---|
| `target_gwh` | Monthly energy target for the segment |
| `consumed_gwh` | Actual energy delivered MTD |
| `gap_gwh` | Energy not delivered (target − consumed) |
| `expected_bill_value` | target_gwh × tariff (₦) |
| `mtd_bill_value` | consumed_gwh × tariff (₦) |
| `gap_bill_value` | Revenue lost due to energy gap (₦) |
| `gap_pct` | 100 − mtd_achievement_pct |

---

## Quick Reference

| # | Endpoint | URL | Slide(s) |
|---|---|---|---|
| 1 | Overview | `GET /api/tmo/overview/` | — |
| 2 | Feeder Dispatch | `GET /api/tmo/energy/dispatch/` | 7, 8, 9 |
| 3 | Energy by Segment | `GET /api/tmo/energy/by-segment/` | 11 |
| 4 | Supply Compliance (per feeder) | `GET /api/tmo/supply/compliance/` | 5 |
| 5 | Collection | `GET /api/tmo/collection/` | 19 |
| 6 | Billing Efficiency | `GET /api/tmo/billing/` | 20 |
| 7 | P&L Targets | `GET /api/tmo/pnl/` | 21 |
| 8 | Minigrids | `GET /api/tmo/minigrids/` | — |
| 9 | All Feeders | `GET /api/tmo/feeders/` | — |
| 10 | Feeder Detail | `GET /api/tmo/feeders/<feeder_slug>/` | — |
| 11 | P&L Mix Volatility | `GET /api/tmo/volatility/` | — |
| 12 | Daily Network Energy | `GET /api/tmo/energy/daily/` | 2, 3 |
| 13 | PEAR | `GET /api/tmo/pear/` | 10 |
| 14 | Compliance Summary | `GET /api/tmo/supply/compliance/summary/` | 6 |
| 15 | Energy by Voltage | `GET /api/tmo/energy/by-voltage/` | 13, 14, 15 |
| 16 | Incidents | `GET /api/tmo/incidents/` | 16 |
| 17 | GCR | `GET /api/tmo/gcr/` | 18 |
