# Load Trend Analytics API - Frontend Integration Guide

## 🎯 Overview

The `/api/technical/overview/` endpoint now returns **analytics** for load trends including peak detection, averages, anomalies, and optional energy target comparison.

---

## 📡 API Endpoint

**Base URL**: `GET /api/technical/overview/`

### Query Parameters

| Parameter | Type | Required | Description | Example |
|-----------|------|----------|-------------|---------|
| `mode` | string | ✅ Yes | Time mode | `"daily"`, `"monthly"`, `"custom"` |
| `from_date` | string | ✅ Yes | Start date (ISO format) | `"2026-02-01"` |
| `to_date` | string | ⚠️ Conditional | End date (required for custom mode) | `"2026-02-15"` |
| `year` | number | ⚠️ Conditional | Year (required for monthly mode) | `2026` |
| `month` | number | ⚠️ Conditional | Month (required for monthly mode) | `1` |
| `feeder_type` | string | ❌ Optional | Voltage level (`"11kv"` or `"33kv"`) | `"11kv"` |
| `feeder` | string | ❌ Optional | Feeder slug for single feeder view | `"gadau-11kv"` |
| `target_energy` | number | ❌ Optional | **NEW!** Target energy in MWh | `10.5` |

---

## ⚡ Global Filter: Voltage Separation
The analytics system now separates **11kV (Distribution)** and **33kV (Primary)** networks.

- **11kV**: Energy distributed to end-customers. Average readings are ~14-16 MWh.
- **33kV**: Energy injected into the network. Average readings are ~50-100 MWh.

> [!IMPORTANT]
> The hierarchy MUST be maintained: **33kV (Upstream) > 11kV (Downstream)**. The `feeder_type` parameter ensures you are comparing apples to apples.

---

## 📦 Response Structure

### TypeScript Interfaces

```typescript
interface LoadTrendResponse {
  load_trend: {
    unit: string;           // Always "MW"
    date: string;           // ISO date or date range
    series: DataPoint[];
    analytics: TrendAnalytics;  // ✨ NEW!
  };
  station_load_trend: {
    unit: string;
    date: string;
    series: DataPoint[];
    analytics: TrendAnalytics;  // ✨ NEW!
  };
  // ... other fields (energy_delivered, hours_of_supply, etc.)
  
  // ✨ NEW! Energy Fidelity Metals
  energy_source: "meter_reading" | "system";  // Whether data is verified or fallback
  reading_estimate_variance: number;         // Delta between reading and system estimate
}

interface DataPoint {
  hour?: number;          // For daily mode (0-23)
  day?: number;           // For monthly mode (1-31)
  date?: string;          // For custom mode (ISO format)
  value: number;          // Load value in MW
  is_anomaly?: boolean;   // ✨ NEW! Present when value is anomalous
}

interface TrendAnalytics {
  // Basic Statistics
  peak: number;                // Highest load value
  peak_time: string;           // When peak occurred ("14:00" or "Day 15")
  average: number;             // Mean load
  min: number;                 // Lowest load value
  min_time: string;            // When min occurred
  std_deviation: number;       // Standard deviation
  variance: number;            // Variance
  range: number;               // Peak - Min
  anomaly_count: number;       // Number of anomalous points
  
  // Energy Target (only if target_energy provided)
  offtake_target?: {
    target_energy: number;     // Target energy in MWh
    actual_energy: number;     // Actual cumulative energy
    expected_rate: number;     // Expected rate per time unit
    variance_pct: number;      // Percentage variance from target
    projected_final: number;   // Projected final energy
    on_track: boolean;         // Whether within ±5% tolerance
    status: "On Track" | "Over" | "Under";
  };
}
```

---

## 🔥 Example Requests & Responses

### 1️⃣ Daily Mode (Hourly Analytics)

**Request:**
```bash
GET /api/technical/overview/?mode=daily&from_date=2026-02-01
```

**Response:**
```json
{
  "load_trend": {
    "unit": "MW",
    "date": "2026-02-01",
    "series": [
      {"hour": 0, "value": 0.95},
      {"hour": 1, "value": 0.82},
      {"hour": 2, "value": 0.67},
      {"hour": 12, "value": 8.45, "is_anomaly": true},
      {"hour": 13, "value": 0.88}
    ],
    "analytics": {
      "peak": 8.45,
      "peak_time": "12:00",
      "average": 1.48,
      "min": 0.08,
      "min_time": "05:00",
      "std_deviation": 2.14,
      "variance": 4.58,
      "range": 8.37,
      "anomaly_count": 1
    }
  }
}
```

---

### 2️⃣ Monthly Mode (Daily Analytics)

**Request:**
```bash
GET /api/technical/overview/?mode=monthly&year=2026&month=1
```

**Response:**
```json
{
  "load_trend": {
    "unit": "MW",
    "date": "2026-01",
    "series": [
      {"day": 1, "value": 1.2},
      {"day": 2, "value": 1.5},
      {"day": 15, "value": 2.8},
      {"day": 31, "value": 1.1}
    ],
    "analytics": {
      "peak": 2.8,
      "peak_time": "Day 15",
      "average": 1.5,
      "min": 0.9,
      "min_time": "Day 7",
      "std_deviation": 0.45,
      "variance": 0.20,
      "range": 1.9,
      "anomaly_count": 0
    }
  }
}
```

---

### 3️⃣ With Energy Target Comparison

**Request:**
```bash
GET /api/technical/overview/?mode=daily&from_date=2026-02-01&target_energy=10
```

**Response:**
```json
{
  "load_trend": {
    "unit": "MW",
    "date": "2026-02-01",
    "series": [...],
    "analytics": {
      "peak": 0.95,
      "peak_time": "00:00",
      "average": 0.48,
      "min": 0.08,
      "min_time": "05:00",
      "std_deviation": 0.34,
      "variance": 0.11,
      "range": 0.87,
      "anomaly_count": 0,
      "offtake_target": {
        "target_energy": 10.0,
        "actual_energy": 11.52,
        "expected_rate": 0.83,
        "variance_pct": 15.2,
        "projected_final": 11.52,
        "on_track": false,
        "status": "Over"
      }
    }
  }
}
```

---

## 💡 Implementation Examples

### React/TypeScript Hook Example

```typescript
import { useState, useEffect } from 'react';

interface UseLoadTrendParams {
  mode: 'daily' | 'monthly' | 'custom';
  fromDate: string;
  toDate?: string;
  year?: number;
  month?: number;
  targetEnergy?: number;
  feeder?: string;
}

export function useLoadTrend(params: UseLoadTrendParams) {
  const [data, setData] = useState<LoadTrendResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      const queryParams = new URLSearchParams({
        mode: params.mode,
        from_date: params.fromDate,
      });

      if (params.toDate) queryParams.append('to_date', params.toDate);
      if (params.year) queryParams.append('year', params.year.toString());
      if (params.month) queryParams.append('month', params.month.toString());
      if (params.feeder) queryParams.append('feeder', params.feeder);
      if (params.targetEnergy) {
        queryParams.append('target_energy', params.targetEnergy.toString());
      }

      const response = await fetch(
        `/api/technical/overview/?${queryParams.toString()}`
      );
      const json = await response.json();
      setData(json);
      setLoading(false);
    };

    fetchData();
  }, [params]);

  return { data, loading };
}
```

### Vue/Composition API Example

```typescript
import { ref, watch } from 'vue';

export function useLoadTrend(params: UseLoadTrendParams) {
  const data = ref<LoadTrendResponse | null>(null);
  const loading = ref(true);

  const fetchData = async () => {
    loading.value = true;
    const queryParams = new URLSearchParams({
      mode: params.mode,
      from_date: params.fromDate,
    });

    // Add optional params
    if (params.targetEnergy) {
      queryParams.append('target_energy', params.targetEnergy.toString());
    }

    const response = await fetch(
      `/api/technical/overview/?${queryParams.toString()}`
    );
    data.value = await response.json();
    loading.value = false;
  };

  watch(() => params, fetchData, { immediate: true, deep: true });

  return { data, loading };
}
```

---

## 🎨 UI Component Suggestions

### Analytics Summary Card

Display the key metrics in a card:

```tsx
function AnalyticsSummary({ analytics }: { analytics: TrendAnalytics }) {
  return (
    <div className="analytics-card">
      <div className="metric">
        <span className="label">Peak Load</span>
        <span className="value">{analytics.peak} MW</span>
        <span className="time">{analytics.peak_time}</span>
      </div>

      <div className="metric">
        <span className="label">Average Load</span>
        <span className="value">{analytics.average} MW</span>
      </div>

      <div className="metric">
        <span className="label">Min Load</span>
        <span className="value">{analytics.min} MW</span>
        <span className="time">{analytics.min_time}</span>
      </div>

      {analytics.anomaly_count > 0 && (
        <div className="metric warning">
          <span className="label">⚠️ Anomalies Detected</span>
          <span className="value">{analytics.anomaly_count}</span>
        </div>
      )}
    </div>
  );
}
```

### Target Comparison Indicator

```tsx
function TargetComparison({ target }: { target: OfftakeTarget }) {
  const statusColor = {
    'On Track': 'green',
    'Over': 'orange',
    'Under': 'red',
  };

  return (
    <div className="target-comparison">
      <div className="progress-bar">
        <div 
          className="progress" 
          style={{ 
            width: `${(target.actual_energy / target.target_energy) * 100}%`,
            backgroundColor: statusColor[target.status]
          }}
        />
      </div>

      <div className="stats">
        <span>Target: {target.target_energy} MWh</span>
        <span>Actual: {target.actual_energy} MWh</span>
        <span className={`status ${target.status.toLowerCase().replace(' ', '-')}`}>
          {target.status} ({target.variance_pct > 0 ? '+' : ''}{target.variance_pct}%)
        </span>
      </div>
    </div>
  );
}
```

### Anomaly Highlighting in Chart

```tsx
function LoadTrendChart({ series }: { series: DataPoint[] }) {
  return (
    <Chart>
      {series.map((point, i) => (
        <DataPoint
          key={i}
          value={point.value}
          className={point.is_anomaly ? 'anomaly' : ''}
          // Highlight anomalous points with different color/marker
        />
      ))}
    </Chart>
  );
}
```

---

## ⚡ Quick Start Checklist

- [ ] Add a **Voltage Level** toggle (11kV / 33kV) to the main dashboard filters.
- [ ] Update API calls to append `?feeder_type=11kv` or `?feeder_type=33kv`.
- [ ] Update TypeScript interfaces with new `energy_source` and `analytics` fields.
- [ ] Add an icon/badge to Energy Delivered values based on `energy_source` (Verified vs Estimated).
- [ ] Label 33kV data as "Network Injection" and 11kV as "Customer Distribution" in the UI.
- [ ] Display analytics summary (peak, average, min) using the new `analytics` object.
- [ ] Highlight anomalous data points in charts using the `is_anomaly` flag.

---

## 📊 Field Descriptions

| Field | Description | Use Case |
|-------|-------------|----------|
| `peak` | Maximum load in period | Capacity planning, peak demand analysis |
| `peak_time` | When peak occurred | Identify high-demand periods |
| `average` | Mean load value | Baseline consumption patterns |
| `min` | Minimum load value | Identify low-demand periods or outages |
| `std_deviation` | Data spread | Measure consistency/volatility |
| `variance` | Statistical variance | Stability indicator |
| `range` | Peak - Min | Total variation span |
| `anomaly_count` | Outlier count | Data quality, unusual events |
| `is_anomaly` | Point-level flag | Visual highlighting in charts |
| `energy_source` | `"meter_reading"` or `"system"` | Data quality indication (Verified vs Estimated) |
| `reading_estimate_variance` | Meter diff - System estimate | Transparency on data correction magnitude |
| `offtake_target` | Target comparison | Performance tracking, goal monitoring |

---

## 🔧 Testing URLs

```bash
# Daily with target
http://localhost:8000/api/technical/overview/?mode=daily&from_date=2026-02-01&target_energy=15

# Monthly
http://localhost:8000/api/technical/overview/?mode=monthly&year=2026&month=1

# Single feeder
http://localhost:8000/api/technical/overview/?mode=daily&from_date=2026-02-01&feeder=gadau-11kv&target_energy=5
```

---

## 📞 Questions?

This feature is backwards compatible - existing code will continue to work. The `analytics` field is always present but optional to use in your UI.
