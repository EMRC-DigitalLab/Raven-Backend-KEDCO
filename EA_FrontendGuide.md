# Raven Backend — Frontend Integration Guide
## Energy Account (EA) + GridLens + Compare Engine

> **Base URL:** `/api/`  
> **Auth:** Token / Session (same as rest of Raven)  
> **All responses:** JSON

---

## Table of Contents

1. [Access Control](#1-access-control)
2. [Shared Response Format](#2-shared-response-format)
3. [Energy Account Module](#3-energy-account-module)
   - [Overview](#31-overview)
   - [States](#32-states)
   - [Districts](#33-districts)
   - [Stations](#34-stations)
   - [Feeders](#35-feeders)
   - [EA Compare Endpoints](#36-ea-compare-endpoints)
4. [GridLens Module](#4-gridlens-module)
5. [Compare Engine — New Additions](#5-compare-engine--new-additions)
6. [Billing Period Params](#6-billing-period-query-params)
7. [Error Responses](#7-error-responses)

---

## 1. Access Control

Every endpoint in this guide is access-gated. Users without the required section grant receive **403**.

| Module | Section name | Who always has access |
|---|---|---|
| Energy Account | `energy_account` | `super_admin`, `admin` |
| GridLens | `grid_lens` | `super_admin`, `admin` |

Regular users need an explicit `UserSectionAccess` or `TemporaryAccess` record granting the relevant section. The backend handles this — the frontend just needs to handle 403 gracefully and prompt the user to contact their admin.

---

## 2. Shared Response Format

### The `metric()` wrapper

Every KPI value in this API is returned inside a `metric` object:

```json
{
  "value": 1234.56,
  "unit": "MWh",
  "mode": "number",
  "explanation": "Human-readable description of what this number means."
}
```

| Field | Type | Notes |
|---|---|---|
| `value` | number \| string \| boolean \| null | The actual data value. Can be `null` if data is unavailable. |
| `unit` | string | e.g. `"MWh"`, `"NGN"`, `"%"`, `""` |
| `mode` | string | Always `"number"` in current build |
| `explanation` | string | Tooltip / info text for the UI |

**Always read `.value` for the number. Never assume the root object is the value.**

### Billing period block

Every response includes a `period` block:

```json
{
  "period": {
    "year": 2024,
    "month": 3,
    "label": "March 2024"
  }
}
```

---

## 3. Energy Account Module

**Base path:** `/api/energy-account/`

### 3.1 Overview

```
GET /api/energy-account/overview/
```

System-wide EA analytics for a billing period.

**Query params:** [see §6](#6-billing-period-query-params)

**Response shape:**

```json
{
  "period": { "year": 2024, "month": 3, "label": "March 2024" },
  "scope": {
    "total_stations": { "value": 42, ... },
    "total_districts": { "value": 8, ... }
  },
  "returns": {
    "total": { "value": 42, ... },
    "submitted": { "value": 38, ... },
    "draft": { "value": 4, ... },
    "late_count": { "value": 3, ... },
    "late_rate_pct": { "value": 7.14, ... },
    "by_status": { "draft": 4, "submitted": 38, ... }
  },
  "energy": {
    "total_billing_mwh": { "value": 85000.0, "unit": "MWh", ... },
    "stream_a_mwh": { "value": 84500.0, "unit": "MWh", ... },
    "stream_b_mwh": { "value": 84200.0, "unit": "MWh", ... },
    "stream_a_vs_b_deviation": { "value": 0.36, "unit": "%", ... },
    "kv33_mwh": { "value": 52000.0, "unit": "MWh", ... },
    "kv11_mwh": { "value": 32200.0, "unit": "MWh", ... },
    "avg_data_completeness": { "value": 96.4, "unit": "%", ... },
    "feeders_with_gaps": { "value": 5, ... },
    "flagged_readings": { "value": 2, ... }
  },
  "revenue": {
    "total_billing_naira": { "value": 1234567890.0, "unit": "NGN", ... },
    "stream_a_naira": { "value": 1230000000.0, "unit": "NGN", ... }
  },
  "reconciliation": {
    "tcn_agreed_count": { "value": 30, ... },
    "tcn_pending_count": { "value": 12, ... },
    "mo_agreed_count": { "value": 28, ... },
    "mo_pending_count": { "value": 14, ... }
  },
  "data_quality": {
    "by_billing_rule": { ... },
    "by_data_source": { ... }
  },
  "breakdown": {
    "by_state": [ ... ],
    "by_district": [ ... ]
  }
}
```

---

### 3.2 States

#### List all states
```
GET /api/energy-account/states/
```

**Query params:** [see §6](#6-billing-period-query-params)

**Response:**
```json
{
  "period": { ... },
  "states": [
    {
      "state": { "slug": "kano", "name": "Kano" },
      "returns": { ... },
      "energy": { ... },
      "revenue": { ... },
      "reconciliation": { ... },
      "data_quality": { ... }
    }
  ]
}
```

#### Single state detail
```
GET /api/energy-account/states/<slug>/
```

Same as list item but also includes:
```json
{
  "scope": {
    "total_stations": { "value": 12, ... },
    "total_districts": { "value": 3, ... }
  },
  "breakdown": {
    "by_district": [ ... ],
    "by_station": [ ... ]
  }
}
```

---

### 3.3 Districts

#### List all districts
```
GET /api/energy-account/districts/
```

**Query params:** `year`, `month`, `state` (slug filter)

**Response:**
```json
{
  "period": { ... },
  "districts": [
    {
      "district": { "slug": "kano-metro", "name": "Kano Metro", "state": "kano" },
      "returns": { ... },
      "energy": { ... },
      "revenue": { ... },
      "reconciliation": { ... },
      "data_quality": { ... }
    }
  ]
}
```

#### Single district detail
```
GET /api/energy-account/districts/<slug>/
```

Also includes:
```json
{
  "scope": { "total_stations": { "value": 4, ... } },
  "breakdown": {
    "by_station": [
      {
        "station": { "slug": "...", "name": "...", "state": "kano" },
        "total_billing_mwh": 1234.0,
        "total_billing_naira": 5678900.0,
        "stream_b_mwh": 1220.0,
        "return_status": "submitted",
        "is_late": false
      }
    ]
  }
}
```

---

### 3.4 Stations

#### List all stations
```
GET /api/energy-account/stations/
```

**Query params:** `year`, `month`, `state`, `district`

**Response:**
```json
{
  "period": { ... },
  "stations": [
    {
      "station": {
        "slug": "kano-north",
        "name": "Kano North",
        "state": "kano",
        "type": "injection"
      },
      "return_status": "submitted",
      "is_late": false,
      "energy": {
        "total_billing_mwh": { "value": 2000.0, "unit": "MWh", ... },
        "stream_a_mwh": { "value": 1980.0, "unit": "MWh", ... },
        "stream_b_mwh": { "value": 1975.0, "unit": "MWh", ... },
        "stream_a_vs_b_deviation": { "value": 0.25, "unit": "%", ... },
        "kv33_mwh": { "value": 1200.0, "unit": "MWh", ... },
        "kv11_mwh": { "value": 775.0, "unit": "MWh", ... },
        "avg_data_completeness": { "value": 97.5, "unit": "%", ... },
        "feeders_with_gaps": { "value": 0, ... },
        "flagged_readings": { "value": 0, ... }
      },
      "revenue": {
        "total_billing_naira": { "value": 45000000.0, "unit": "NGN", ... },
        "stream_a_naira": { "value": 44800000.0, "unit": "NGN", ... }
      }
    }
  ]
}
```

#### Single station detail
```
GET /api/energy-account/stations/<slug>/
```

Full detail — includes individual meter readings, feeder technical energy rows, TCN/MO reconciliation, and latest meter check record.

```json
{
  "period": { ... },
  "station": {
    "slug": "kano-north",
    "name": "Kano North",
    "state": "kano",
    "type": "injection",
    "status": "active"
  },
  "return_status": "submitted",
  "is_late": false,
  "late_reason": null,
  "submitted_at": "2024-03-06T10:23:00+01:00",

  "returns": { ... },
  "energy": { ... },
  "revenue": { ... },
  "reconciliation": { ... },
  "data_quality": { ... },

  "meter_inventory": {
    "transformer_meters": { "value": 2, ... },
    "feeder_meters": { "value": 5, ... }
  },

  "readings": [
    {
      "meter_no": "MTR-001",
      "meter_owner_type": "transformer",
      "feeder": null,
      "transformer": "trafo-kano-north-t1",
      "previous_reading": 120000.0,
      "present_reading": 122500.0,
      "energy_mwh": 2500.0,
      "billing_mwh": 2500.0,
      "billing_naira": 56250000.0,
      "deviation_pct": 0.15,
      "is_flagged": false,
      "data_source": "manual_entry",
      "billing_rule": "within_threshold",
      "nbet_rate_snapshot": 22500.0,
      "remarks": null
    }
  ],

  "feeder_technical": [
    {
      "feeder": "kano-north-f1",
      "feeder_type": "33KV",
      "energy_mwh": 1200.0,
      "data_completeness_pct": 100.0,
      "days_with_data": 31,
      "total_days_in_period": 31,
      "has_gaps": false,
      "gap_notes": "",
      "data_source": "auto_technical"
    }
  ],

  "tcn_reconciliation": {
    "kedco_figure_mwh": 2000.0,
    "kedco_figure_naira": 45000000.0,
    "tcn_figure_mwh": 2010.0,
    "tcn_figure_naira": 45225000.0,
    "difference_mwh": -10.0,
    "difference_pct": -0.5,
    "agreed_figure_mwh": 2005.0,
    "status": "agreed",
    "resolution_notes": "Agreed on midpoint",
    "tcn_contact_name": "John Doe"
  },

  "mo_reconciliation": {
    "mo_figure_mwh": 2000.0,
    "kedco_figure_mwh": 2000.0,
    "difference_mwh": 0.0,
    "status": "agreed",
    "notes": null
  },

  "latest_meter_check": {
    "check_date": "2024-02-15",
    "polarity": "correct",
    "power_factor": 0.95,
    "voltage_r": 240.5,
    "voltage_y": 239.8,
    "voltage_b": 241.0,
    "ct_ratio": "200/5",
    "battery_level": "good",
    "risk_level": "low",
    "remarks": null
  }
}
```

---

### 3.5 Feeders

#### List all feeders
```
GET /api/energy-account/feeders/
```

**Query params:** `year`, `month`, `station`, `district`, `state`, `type` (33KV or 11KV)

**Response:**
```json
{
  "period": { ... },
  "summary": {
    "total_feeders": { "value": 87, ... },
    "total_stream_b_mwh": { "value": 84200.0, "unit": "MWh", ... },
    "kv33_mwh": { "value": 52000.0, "unit": "MWh", ... },
    "kv11_mwh": { "value": 32200.0, "unit": "MWh", ... },
    "avg_data_completeness": { "value": 96.4, "unit": "%", ... },
    "feeders_with_gaps": { "value": 5, ... }
  },
  "feeders": [
    {
      "feeder": {
        "slug": "kano-north-f1",
        "name": "Kano North F1",
        "voltage_level": "33kv",
        "feeder_type": "33KV",
        "station": "kano-north",
        "state": "kano"
      },
      "return_status": "submitted",
      "energy": {
        "stream_b_mwh": { "value": 1200.0, "unit": "MWh", ... },
        "stream_a_mwh": { "value": null, "unit": "MWh", ... },
        "stream_a_vs_b_deviation": { "value": null, "unit": "%", ... },
        "billing_mwh": { "value": null, "unit": "MWh", ... },
        "billing_naira": { "value": null, "unit": "NGN", ... }
      },
      "data_quality": {
        "data_completeness_pct": { "value": 100.0, "unit": "%", ... },
        "days_with_data": { "value": 31, ... },
        "total_days_in_period": { "value": 31, ... },
        "has_gaps": { "value": false, ... },
        "flagged_readings": { "value": 0, ... },
        "data_source": { "value": "auto_technical", ... }
      }
    }
  ]
}
```

#### Single feeder detail
```
GET /api/energy-account/feeders/<slug>/
```

Same as list item but also includes an 8-week `weekly_trend` array:

```json
{
  "weekly_trend": [
    {
      "week_start_date": "2024-02-19",
      "week_end_date": "2024-02-25",
      "energy_mwh": 280.5,
      "prior_week_energy_mwh": 265.2,
      "delta_pct": 5.77,
      "is_anomaly": false,
      "data_source": "auto_technical",
      "notes": null
    }
  ]
}
```

---

### 3.6 EA Compare Endpoints

#### Station vs Feeders
```
GET /api/energy-account/compare/station-vs-feeders/<slug>/
```

Compares the station transformer meter (Stream A) against each 33kV feeder's Stream B.

**Query params:** `year`, `month`

**Response:**
```json
{
  "compare_type": "station_vs_feeders",
  "period": { ... },
  "station": {
    "slug": "kano-north",
    "name": "Kano North",
    "state": "kano",
    "status": "submitted",
    "is_late": false
  },
  "methodology": {
    "transmission_side": { "value": null, "explanation": "Stream A — Physical transformer..." },
    "feeder_side": { "value": null, "explanation": "Stream B — Feeder technical energy..." }
  },
  "summary": {
    "transmission_total_mwh": { "value": 2000.0, "unit": "MWh", ... },
    "feeder_sum_33kv_mwh": { "value": 1975.0, "unit": "MWh", ... },
    "deviation_pct": { "value": 1.27, "unit": "%", ... },
    "billing_rule_triggered": { "value": "within_threshold", ... },
    "station_billing_mwh": { "value": 2000.0, "unit": "MWh", ... },
    "station_billing_naira": { "value": 45000000.0, "unit": "NGN", ... }
  },
  "transmission_side": [
    {
      "label": "Kano North T1",
      "meter_no": "MTR-001",
      "energy_mwh": { "value": 2000.0, "unit": "MWh", ... },
      "billing_mwh": { "value": 2000.0, "unit": "MWh", ... },
      "billing_rule": "within_threshold",
      "is_flagged": false,
      "deviation_pct": 0.25,
      "data_source": "manual_entry",
      "nbet_snapshot": 22500.0
    }
  ],
  "feeder_side": [
    {
      "feeder": { "slug": "kano-north-f1", "name": "Kano North F1", "district": "kano-metro" },
      "stream_b_mwh": { "value": 1200.0, "unit": "MWh", ... },
      "check_meter_mwh": { "value": null, "unit": "MWh", ... },
      "data_completeness_pct": { "value": 100.0, "unit": "%", ... },
      "has_gaps": false,
      "data_source": "auto_technical",
      "pct_of_transformer": { "value": 60.0, "unit": "%", ... }
    }
  ]
}
```

---

#### 33kV vs 11kV Tier Comparison
```
GET /api/energy-account/compare/kv33-vs-kv11/
```

**Query params:** `year`, `month`, `station`, `state`, `district`

**Response:**
```json
{
  "compare_type": "kv33_vs_kv11",
  "period": { ... },
  "scope": {
    "total_stations": { "value": 42, ... },
    "total_33kv_feeders": { "value": 55, ... },
    "total_11kv_feeders": { "value": 210, ... }
  },
  "methodology": {
    "kv33_side": { "value": null, "explanation": "33kV feeder Stream B energy..." },
    "kv11_side": { "value": null, "explanation": "11kV feeder Stream B energy..." },
    "tier_gap": { "value": null, "explanation": "The difference between 33kV and 11kV..." }
  },
  "summary": {
    "total_33kv_mwh": { "value": 52000.0, "unit": "MWh", ... },
    "total_11kv_mwh": { "value": 32200.0, "unit": "MWh", ... },
    "tier_gap_mwh": { "value": 19800.0, "unit": "MWh", ... },
    "tier_gap_pct": { "value": 38.08, "unit": "%", ... },
    "avg_completeness_33kv": { "value": 97.2, "unit": "%", ... },
    "avg_completeness_11kv": { "value": 95.1, "unit": "%", ... },
    "feeders_with_gaps_33kv": { "value": 2, ... },
    "feeders_with_gaps_11kv": { "value": 8, ... }
  },
  "by_station": [
    {
      "station": { "slug": "kano-north", "name": "Kano North" },
      "kv33": {
        "energy_mwh": { "value": 1200.0, "unit": "MWh", ... },
        "feeder_count": { "value": 2, ... },
        "avg_data_completeness": { "value": 100.0, "unit": "%", ... }
      },
      "kv11": {
        "energy_mwh": { "value": 800.0, "unit": "MWh", ... },
        "feeder_count": { "value": 8, ... },
        "avg_data_completeness": { "value": 95.5, "unit": "%", ... }
      },
      "tier_gap": {
        "gap_mwh": { "value": 400.0, "unit": "MWh", ... },
        "gap_pct": { "value": 33.33, "unit": "%", ... }
      }
    }
  ]
}
```

---

#### Full Alignment Cascade ⭐
```
GET /api/energy-account/compare/full-alignment/
```

The most comprehensive EA endpoint. Shows the complete **Transformer → 33kV → 11kV** energy cascade for every station in scope — every meter, every feeder, every loss layer, every alignment flag.

**Query params:** `year`, `month`, `station`, `state`, `district`

> If no filter is applied, returns all stations network-wide.

**Response:**
```json
{
  "compare_type": "full_alignment",
  "period": { "year": 2024, "month": 3, "label": "March 2024" },

  "summary": {
    "station_count": { "value": 42, ... },
    "transformer_total_mwh": { "value": 85000.0, "unit": "MWh", ... },
    "kv33_total_mwh": { "value": 52000.0, "unit": "MWh", ... },
    "kv11_total_mwh": { "value": 32200.0, "unit": "MWh", ... },

    "metering_gap": {
      "mwh": { "value": 33000.0, "unit": "MWh", ... },
      "pct": { "value": 38.82, "unit": "%", ... },
      "status": "critical"
    },
    "tier_gap": {
      "mwh": { "value": 19800.0, "unit": "MWh", ... },
      "pct": { "value": 38.08, "unit": "%", ... },
      "status": "high"
    },
    "total_cascade_loss": {
      "mwh": { "value": 52800.0, "unit": "MWh", ... },
      "pct": { "value": 62.12, "unit": "%", ... }
    },

    "attention_flags": {
      "stations_critical_metering_gap": { "value": 3, ... },
      "stations_high_tier_gap": { "value": 7, ... },
      "misaligned_33kv_feeders": { "value": 2, ... },
      "kv33_feeders_with_data_gaps": { "value": 4, ... },
      "kv11_feeders_with_data_gaps": { "value": 12, ... }
    }
  },

  "stations": [
    {
      "station": {
        "slug": "kano-north",
        "name": "Kano North",
        "state": "kano",
        "type": "injection",
        "status": "active"
      },
      "return_status": "submitted",
      "is_late": false,

      "transmission_layer": {
        "total_billing_mwh": { "value": 2000.0, "unit": "MWh", ... },
        "total_energy_mwh": { "value": 1998.5, "unit": "MWh", ... },
        "meter_count": { "value": 1, ... },
        "meters": [
          {
            "label": "Kano North T1",
            "meter_no": "MTR-001",
            "energy_mwh": { "value": 1998.5, "unit": "MWh", ... },
            "billing_mwh": { "value": 2000.0, "unit": "MWh", ... },
            "billing_rule": "within_threshold",
            "deviation_pct": 0.25,
            "is_flagged": false,
            "data_source": "manual_entry",
            "nbet_snapshot": 22500.0
          }
        ]
      },

      "kv33_layer": {
        "total_mwh": { "value": 1975.0, "unit": "MWh", ... },
        "feeder_count": { "value": 2, ... },
        "feeders_with_gaps": { "value": 0, ... },
        "misaligned_feeders": { "value": 0, ... },
        "feeders": [
          {
            "feeder": { "slug": "kano-north-f1", "name": "Kano North F1", "district": "kano-metro" },
            "stream_b_mwh": { "value": 1200.0, "unit": "MWh", ... },
            "check_meter_mwh": { "value": 1195.0, "unit": "MWh", ... },
            "check_vs_stream_b_pct": { "value": 0.42, "unit": "%", ... },
            "feeder_alignment_status": "aligned",
            "pct_of_transformer": { "value": 60.0, "unit": "%", ... },
            "data_completeness_pct": { "value": 100.0, "unit": "%", ... },
            "has_gaps": false,
            "data_source": "auto_technical"
          },
          {
            "feeder": { "slug": "kano-north-f2", "name": "Kano North F2", "district": "kano-south" },
            "stream_b_mwh": { "value": 775.0, "unit": "MWh", ... },
            "check_meter_mwh": { "value": null, "unit": "MWh", ... },
            "check_vs_stream_b_pct": { "value": null, "unit": "%", ... },
            "feeder_alignment_status": "no_check_meter",
            "pct_of_transformer": { "value": 38.75, "unit": "%", ... },
            "data_completeness_pct": { "value": 100.0, "unit": "%", ... },
            "has_gaps": false,
            "data_source": "auto_technical"
          }
        ]
      },

      "kv11_layer": {
        "total_mwh": { "value": 800.0, "unit": "MWh", ... },
        "feeder_count": { "value": 8, ... },
        "feeders_with_gaps": { "value": 0, ... },
        "feeders": [
          {
            "feeder": { "slug": "kano-north-11f1", "name": "Kano North 11F1", "district": "kano-metro" },
            "stream_b_mwh": { "value": 100.0, "unit": "MWh", ... },
            "pct_of_kv33_total": { "value": 5.06, "unit": "%", ... },
            "data_completeness_pct": { "value": 98.5, "unit": "%", ... },
            "has_gaps": false,
            "data_source": "auto_technical"
          }
        ]
      },

      "loss_cascade": {
        "metering_gap": {
          "label": "Transformer → 33kV (Metering Gap)",
          "mwh": { "value": 25.0, "unit": "MWh", ... },
          "pct": { "value": 1.25, "unit": "%", ... },
          "status": "within_threshold"
        },
        "tier_gap": {
          "label": "33kV → 11kV (Distribution Tier Gap)",
          "mwh": { "value": 1175.0, "unit": "MWh", ... },
          "pct": { "value": 59.49, "unit": "%", ... },
          "status": "high"
        },
        "total_cascade_loss": {
          "label": "Transformer → 11kV (Full Cascade)",
          "mwh": { "value": 1200.0, "unit": "MWh", ... },
          "pct": { "value": 60.0, "unit": "%", ... }
        }
      },

      "alignment": {
        "metering_gap_status": "within_threshold",
        "tier_gap_status": "high",
        "billing_rule_triggered": "within_threshold",
        "station_billing_mwh": { "value": 2000.0, "unit": "MWh", ... },
        "station_billing_naira": { "value": 45000000.0, "unit": "NGN", ... }
      }
    }
  ]
}
```

**Alignment status values:**

| Field | Possible values |
|---|---|
| `metering_gap_status` | `within_threshold` (≤2%) · `elevated` (2–5%) · `critical` (>5%) |
| `tier_gap_status` | `normal` (≤10%) · `elevated` (10–20%) · `high` (>20%) |
| `billing_rule_triggered` | `within_threshold` · `max_applied` |
| `feeder_alignment_status` | `aligned` (≤2%) · `minor_divergence` (2–5%) · `divergent` (>5%) · `no_check_meter` |

> **Note on 2% rule:** The billing rule is triggered when `|Stream A − Stream B| / Stream B > 2%`. When triggered (`max_applied`), the **higher** of Stream A or Stream B is used for the NBET billing figure. This is applied at every level — per feeder, per station, and up through the hierarchy.

---

## 4. GridLens Module

**Base path:** `/api/analytics/grid-lens/`

GridLens is the loss decomposition module. It traces energy from TCN input → feeder distribution → commercial billing → collection and shows every loss layer.

> **Commercial billed / collected figures** (from `MonthlyOverviewSummary`) are only available at the **system-wide overview level**. State, district, and station endpoints show the EA + Stream B layers only.

### Energy chain

```
EA Received (TCN-agreed)
    ↓  metering gap         = EA − Stream B
Feeder Distributed (Stream B)
    ↓  distribution loss    = Stream B − Commercially Billed   [overview only]
Commercially Billed
    ↓  collection loss      = Billed − Collected               [overview only]
Commercially Collected
```

---

### 4.1 System Overview
```
GET /api/analytics/grid-lens/
```

**Query params:** `year`, `month`

Full 4-layer chain + by-state breakdown.

**Response:**
```json
{
  "period": { ... },
  "module": "GridLens",

  "energy_chain": {
    "ea_received_mwh": { "value": 85000.0, "unit": "MWh", ... },
    "feeder_distributed_mwh": { "value": 52000.0, "unit": "MWh", ... },
    "commercially_billed_mwh": { "value": 40000.0, "unit": "MWh", ... },
    "commercially_collected_mwh": { "value": 36000.0, "unit": "MWh", ... }
  },

  "loss_decomposition": {
    "metering_gap": {
      "mwh": { "value": 33000.0, "unit": "MWh", ... },
      "pct": { "value": 38.82, "unit": "%", ... }
    },
    "distribution_loss": {
      "mwh": { "value": 12000.0, "unit": "MWh", ... },
      "pct": { "value": 23.08, "unit": "%", ... }
    },
    "collection_loss": {
      "mwh": { "value": 4000.0, "unit": "MWh", ... },
      "pct": { "value": 10.0, "unit": "%", ... }
    },
    "total_atcc_loss": {
      "mwh": { "value": 49000.0, "unit": "MWh", ... },
      "pct": { "value": 57.65, "unit": "%", ... }
    }
  },

  "efficiency": {
    "transmission_efficiency": { "value": 61.18, "unit": "%", ... },
    "billing_efficiency": { "value": 76.92, "unit": "%", ... },
    "collection_efficiency": { "value": 90.0, "unit": "%", ... },
    "overall_efficiency": { "value": 42.35, "unit": "%", ... }
  },

  "data_context": {
    "ea_return_count": { "value": 42, ... },
    "late_returns": { "value": 3, ... },
    "total_feeder_records": { "value": 265, ... },
    "kv33_mwh": { "value": 52000.0, "unit": "MWh", ... },
    "kv11_mwh": { "value": 32200.0, "unit": "MWh", ... },
    "avg_stream_b_completeness": { "value": 96.4, "unit": "%", ... },
    "feeders_with_gaps": { "value": 5, ... }
  },

  "breakdown": {
    "by_state": [
      {
        "state": { "slug": "kano", "name": "Kano" },
        "ea_received_mwh": { "value": 45000.0, "unit": "MWh", ... },
        "feeder_distributed_mwh": { "value": 28000.0, "unit": "MWh", ... },
        "metering_gap_mwh": { "value": 17000.0, "unit": "MWh", ... },
        "metering_gap_pct": { "value": 37.78, "unit": "%", ... },
        "transmission_efficiency": { "value": 62.22, "unit": "%", ... },
        "return_count": { "value": 22, ... },
        "late_returns": { "value": 2, ... }
      }
    ]
  }
}
```

---

### 4.2 States
```
GET /api/analytics/grid-lens/states/           ← list
GET /api/analytics/grid-lens/states/<slug>/    ← detail with station breakdown
```

**Query params:** `year`, `month`

List item shape:
```json
{
  "state": { "slug": "kano", "name": "Kano" },
  "ea_received_mwh": { "value": 45000.0, "unit": "MWh", ... },
  "feeder_distributed_mwh": { "value": 28000.0, "unit": "MWh", ... },
  "metering_gap_mwh": { "value": 17000.0, "unit": "MWh", ... },
  "metering_gap_pct": { "value": 37.78, "unit": "%", ... },
  "transmission_efficiency": { "value": 62.22, "unit": "%", ... },
  "return_count": { "value": 22, ... },
  "late_returns": { "value": 2, ... },
  "avg_stream_b_completeness": { "value": 96.1, "unit": "%", ... }
}
```

Detail also includes `energy_chain`, `loss_decomposition`, `efficiency`, `data_context`, and `breakdown.by_station`.

The `commercially_billed_mwh` and `commercially_collected_mwh` fields will have `"value": null` at this level — this is expected. See §4.1 for system-level commercial figures.

---

### 4.3 Districts
```
GET /api/analytics/grid-lens/districts/           ← list, optional ?state=<slug>
GET /api/analytics/grid-lens/districts/<slug>/    ← detail with station breakdown
```

Same structure as states, scoped to business district.

---

### 4.4 Stations
```
GET /api/analytics/grid-lens/stations/           ← list, optional ?state=, ?district=
GET /api/analytics/grid-lens/stations/<slug>/    ← detail with per-feeder breakdown
```

List item shape:
```json
{
  "station": { "slug": "kano-north", "name": "Kano North", "state": "kano", "type": "injection" },
  "ea_received_mwh": { "value": 2000.0, "unit": "MWh", ... },
  "feeder_distributed_mwh": { "value": 1975.0, "unit": "MWh", ... },
  "metering_gap_mwh": { "value": 25.0, "unit": "MWh", ... },
  "metering_gap_pct": { "value": 1.25, "unit": "%", ... },
  "transmission_efficiency": { "value": 98.75, "unit": "%", ... },
  "kv33_mwh": { "value": 1200.0, "unit": "MWh", ... },
  "kv11_mwh": { "value": 775.0, "unit": "MWh", ... },
  "avg_stream_b_completeness": { "value": 100.0, "unit": "%", ... },
  "feeders_with_gaps": { "value": 0, ... }
}
```

Detail also includes `breakdown.by_feeder` with each feeder's Stream B share.

---

## 5. Compare Engine — New Additions

**Base path:** `/api/analytics/compare/`

The existing compare engine now supports **GridLens metrics** and the **`station`** entity type. Everything goes through the existing two endpoints.

### 5.1 Available Metrics
```
GET /api/analytics/compare/available/
```

Returns the metrics catalogue filtered to the user's access. GridLens metrics appear under the `grid_lens` module key in the response — only visible if the user has `grid_lens` section access.

```json
{
  "metrics": {
    "grid_lens": [
      { "key": "gl_ea_received_mwh",         "label": "EA Received (MWh)",               "unit": "MWh", "entity_types": ["state", "district", "station"] },
      { "key": "gl_feeder_distributed_mwh",   "label": "Feeder Distributed — Stream B",   "unit": "MWh", "entity_types": ["state", "district", "station"] },
      { "key": "gl_metering_gap_mwh",         "label": "Metering Gap (MWh)",               "unit": "MWh", "entity_types": ["state", "district", "station"] },
      { "key": "gl_metering_gap_pct",         "label": "Metering Gap (%)",                 "unit": "%",   "entity_types": ["state", "district", "station"] },
      { "key": "gl_transmission_efficiency",  "label": "Transmission Efficiency (%)",      "unit": "%",   "entity_types": ["state", "district", "station"] },
      { "key": "gl_kv33_mwh",                "label": "33kV Stream B (MWh)",             "unit": "MWh", "entity_types": ["state", "district", "station"] },
      { "key": "gl_kv11_mwh",                "label": "11kV Stream B (MWh)",             "unit": "MWh", "entity_types": ["state", "district", "station"] },
      { "key": "gl_tier_gap_pct",             "label": "33kV → 11kV Tier Gap (%)",        "unit": "%",   "entity_types": ["state", "district", "station"] },
      { "key": "gl_stream_b_completeness",    "label": "Stream B Data Completeness (%)",  "unit": "%",   "entity_types": ["state", "district", "station"] }
    ],
    "energy_account": [
      { "key": "ea_billing_mwh",         "entity_types": ["station", "feeder"], ... },
      { "key": "ea_stream_a_mwh",        "entity_types": ["station"], ... },
      { "key": "ea_stream_b_mwh",        "entity_types": ["station", "feeder"], ... },
      { "key": "ea_stream_deviation_pct","entity_types": ["station"], ... },
      { "key": "ea_kv33_mwh",            "entity_types": ["station"], ... },
      { "key": "ea_kv11_mwh",            "entity_types": ["station"], ... },
      { "key": "ea_billing_naira",       "entity_types": ["station", "feeder"], ... },
      { "key": "ea_data_completeness",   "entity_types": ["station", "feeder"], ... },
      { "key": "ea_late_rate",           "entity_types": ["station"], ... },
      { "key": "ea_return_status",       "entity_types": ["station"], ... }
    ]
  }
}
```

### 5.2 Run Compare — GridLens Entity Comparison

Compare **metering gap** or **transmission efficiency** across multiple states, districts, or stations:

```
POST /api/analytics/compare/
```

```json
{
  "compare_mode": "entities",
  "entity_type": "station",
  "entity_ids": ["<station-uuid-1>", "<station-uuid-2>", "<station-uuid-3>"],
  "metrics": ["gl_metering_gap_pct", "gl_transmission_efficiency", "gl_kv33_mwh", "gl_kv11_mwh", "gl_tier_gap_pct"],
  "from_date": "2024-01-01",
  "to_date": "2024-03-31",
  "granularity": "monthly",
  "include_trend": true
}
```

Or compare states:
```json
{
  "compare_mode": "entities",
  "entity_type": "state",
  "entity_ids": ["<kano-uuid>", "<jigawa-uuid>"],
  "metrics": ["gl_metering_gap_pct", "gl_transmission_efficiency", "gl_stream_b_completeness"],
  "from_date": "2024-01-01",
  "to_date": "2024-06-30",
  "granularity": "monthly"
}
```

**Response shape** (same as all compare responses):
```json
{
  "compare_mode": "entities",
  "entity_type": "station",
  "granularity": "monthly",
  "period": { "from_date": "2024-01-01", "to_date": "2024-03-31" },
  "metrics_returned": ["gl_metering_gap_pct", "gl_transmission_efficiency"],
  "metrics_denied": [],
  "results": [
    {
      "entity": { "id": "<uuid>", "name": "Kano North", "type": "station" },
      "data": {
        "gl_metering_gap_pct": 1.25,
        "gl_transmission_efficiency": 98.75
      },
      "trend": [
        {
          "period": "2024-01",
          "from_date": "2024-01-01",
          "to_date": "2024-01-31",
          "gl_metering_gap_pct": 1.10,
          "gl_transmission_efficiency": 98.90
        }
      ]
    }
  ]
}
```

### 5.3 Run Compare — EA Metrics on Stations

```json
{
  "compare_mode": "entities",
  "entity_type": "station",
  "entity_ids": ["<uuid-1>", "<uuid-2>"],
  "metrics": ["ea_billing_mwh", "ea_stream_a_mwh", "ea_stream_b_mwh", "ea_stream_deviation_pct", "ea_late_rate"],
  "from_date": "2024-01-01",
  "to_date": "2024-12-31",
  "granularity": "monthly"
}
```

### 5.4 Period Comparison — One Station Over Time

```json
{
  "compare_mode": "periods",
  "entity_type": "station",
  "entity_id": "<station-uuid>",
  "metrics": ["gl_metering_gap_pct", "gl_transmission_efficiency", "ea_billing_mwh"],
  "periods": [
    { "label": "Q1 2024", "from_date": "2024-01-01", "to_date": "2024-03-31" },
    { "label": "Q2 2024", "from_date": "2024-04-01", "to_date": "2024-06-30" },
    { "label": "Q3 2024", "from_date": "2024-07-01", "to_date": "2024-09-30" }
  ]
}
```

### 5.5 metrics_denied

If the user doesn't have access to a module, the affected metrics are returned in `metrics_denied` rather than silently omitted:

```json
{
  "metrics_denied": [
    { "metric": "gl_metering_gap_pct", "module": "grid_lens", "reason": "No access to the grid_lens module" }
  ]
}
```

The frontend should check `metrics_denied` and grey out / hide those fields in the UI.

---

## 6. Billing Period Query Params

EA and GridLens endpoints use **month + year** (not date ranges):

| Param | Type | Default | Example |
|---|---|---|---|
| `year` | int | Current year | `?year=2024` |
| `month` | int (1–12) | Current month | `?month=3` |

**Both together:** `?year=2024&month=3` → March 2024

---

## 7. Error Responses

| Status | When |
|---|---|
| `400` | Bad request — missing required param, invalid date, invalid entity type |
| `403` | User doesn't have access to the required section |
| `404` | Requested slug (station, feeder, state, district) not found |
| `404` (in body) | No monthly return found for the station + period in `station_vs_feeders` |
| `500` | Server error — details in `error` field |

**403 shape:**
```json
{
  "detail": "You do not have access to the Energy Account module. Contact your administrator to request access."
}
```

**404 shape:**
```json
{ "detail": "Not found." }
```

**400 shape (compare engine):**
```json
{ "error": "entity_type must be one of: state, district, feeder, band, station" }
```

---

*Generated for the Raven frontend integration team.*
