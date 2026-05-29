# Raven Report Engine — Frontend Integration Guide

**Target audience:** Frontend engineers (React + TypeScript)
**Backend endpoint:** `POST /api/reports/generate/data/`
**PDF generation:** Client-side using `@react-pdf/renderer`
**Last updated:** May 2026

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Supporting Endpoints](#2-supporting-endpoints)
3. [Generating a Report — Full Request/Response](#3-generating-a-report--full-requestresponse)
4. [Role-Based Section Denial](#4-role-based-section-denial)
5. [Loading States and Error Handling](#5-loading-states-and-error-handling)
6. [Visual Indicators — Frontend Responsibility](#6-visual-indicators--frontend-responsibility)
7. [AI Insights — Display Patterns](#7-ai-insights--display-patterns)
8. [Comparison Sections](#8-comparison-sections)
9. [PDF Generation with @react-pdf/renderer](#9-pdf-generation-with-react-pdfrenderer)
10. [Report Builder UI — Recommended Flow](#10-report-builder-ui--recommended-flow)
11. [TypeScript Types Reference](#11-typescript-types-reference)
12. [Complete Working Example](#12-complete-working-example)

---

## 1. Architecture Overview

The report engine uses a **data-only** model. The backend returns structured JSON; the frontend is fully responsible for:

- Rendering section data into readable UI components
- Computing all visual indicators (trend arrows, color coding, delta percentages, rating badges)
- Generating the PDF using `@react-pdf/renderer`

The backend handles:
- Aggregating multi-source data per section type
- Role-based section filtering (denied sections never appear in the response)
- Calling the AI insights service (Claude) when opted in
- Writing an audit record to `GeneratedReport` on every call

```
Frontend                         Backend (Django)
--------                         ----------------
Build request JSON
  |
  POST /api/reports/generate/data/
  |                               --> Role check: strip denied sections
  |                               --> ReportDataService: fetch section data
  |                               --> AI insights (optional, 5-25s extra)
  |                               --> Write GeneratedReport audit row
  <-- JSON response with sections + AI
  |
Render sections in UI
  |
User clicks "Download PDF"
  |
@react-pdf/renderer generates PDF in the browser
```

---

## 2. Supporting Endpoints

All endpoints are prefixed `/api/reports/`.

### 2.1 Available Sections

```
GET /api/reports/sections/available/
```

Returns the section types the **current user** is allowed to request. Fetch this when building the section picker in the report builder.

**Response:**
```json
{
  "sections": [
    {
      "section_type": "technical_metrics",
      "display_name": "Technical Metrics Cards",
      "description": "Key technical metrics displayed as cards",
      "category": "technical",
      "supports_chart": false,
      "config_options": { ... }
    }
  ],
  "categories": [
    { "id": "general",    "name": "General" },
    { "id": "technical",  "name": "Technical" },
    { "id": "hr",         "name": "Human Resources" },
    { "id": "commercial", "name": "Commercial" },
    { "id": "financial",  "name": "Financial" },
    { "id": "comparison", "name": "Comparisons" }
  ]
}
```

Use `categories` to group sections in the section picker. Never show sections that are not in this response — the server-side role check enforces the same list, so requesting a denied section just wastes a round-trip.

### 2.2 Filter Options

```
GET /api/reports/filters/options/
```

Returns all available filter values. Fetch once on mount and cache client-side.

**Response:**
```json
{
  "states": [{ "id": 1, "name": "Kano" }],
  "districts": [{ "id": 1, "name": "Fagge", "state__name": "Kano", "state_id": 1 }],
  "substations": [{ "id": 1, "name": "Dakata 132/33kV" }],
  "bands": [{ "id": 1, "name": "A", "description": "20+ hrs/day" }],
  "feeders": [{
    "id": 1,
    "name": "Aba Road 11kV",
    "band__name": "A",
    "substation__name": "Dakata 132/33kV",
    "business_district__name": "Fagge",
    "substation_id": 1,
    "business_district_id": 1,
    "band_id": 1,
    "voltage_level": "11kv"
  }],
  "voltage_levels": [
    { "id": "11kv", "name": "11kV Feeders" },
    { "id": "33kv", "name": "33kV Feeders" }
  ]
}
```

### 2.3 Report History

```
GET /api/reports/history/
```

Returns the current user's previously generated reports (newest first). Use this to show a "Recent reports" list and allow re-running a report with the same configuration.

### 2.4 Templates

```
GET  /api/reports/templates/           -- list user's + public templates
POST /api/reports/templates/           -- create a new template
GET  /api/reports/templates/<uuid>/    -- retrieve a template
PUT  /api/reports/templates/<uuid>/    -- update
DEL  /api/reports/templates/<uuid>/    -- delete (own templates only)
POST /api/reports/templates/<uuid>/clone/  -- duplicate a template
```

Templates store the report configuration (sections, filters, title) so users can regenerate reports without re-selecting everything. When the user loads a template, populate the report builder form from `template.default_filters` and `template.sections`.

### 2.5 Section Data Preview

```
POST /api/reports/preview/section/    -- single section preview
POST /api/reports/preview/all/        -- all sections preview
```

Use the single-section preview to show a live data preview as users configure each section in the builder. Note: preview endpoints do not call the AI or write audit records.

---

## 3. Generating a Report — Full Request/Response

### 3.1 Request

```
POST /api/reports/generate/data/
Content-Type: application/json
Authorization: Bearer <token>
```

```typescript
interface ReportGenerateRequest {
  report_title: string;
  report_subtitle?: string;
  orientation: 'portrait' | 'landscape';
  company_name?: string;
  sections: ReportSectionInput[];
  filters: ReportFilters;
  include_ai_insights?: boolean;   // default false — adds 5-25 seconds
  include_ai_summary?: boolean;    // default true when AI is enabled
}

interface ReportSectionInput {
  section_type: string;
  title?: string;
  config?: Record<string, unknown>;
}

interface ReportFilters {
  from_date: string;   // 'YYYY-MM-DD'
  to_date: string;     // 'YYYY-MM-DD'
  states?: number[];
  districts?: number[];
  feeders?: number[];
  substations?: number[];
  bands?: number[];
}
```

**Minimal working request:**
```typescript
const request: ReportGenerateRequest = {
  report_title: "October 2025 Performance Report",
  report_subtitle: "Kano Electricity Distribution Company",
  orientation: "portrait",
  company_name: "KANO ELECTRICITY DISTRIBUTION COMPANY",
  sections: [
    { section_type: "cover_page", config: {} },
    { section_type: "technical_metrics", config: {} },
    {
      section_type: "entity_comparison",
      config: {
        entity_type: "feeder",
        entity_ids: [1, 2, 3],
        metrics: ["hours_of_supply", "at_c_loss"]
      }
    }
  ],
  filters: {
    from_date: "2025-10-01",
    to_date: "2025-10-31"
  },
  include_ai_insights: true,
  include_ai_summary: true
};
```

### 3.2 Response

```typescript
interface ReportGenerateResponse {
  report_id: string;               // UUID — audit trail reference
  report_title: string;
  report_subtitle: string;
  orientation: 'portrait' | 'landscape';
  company_name: string;
  generated_at: string;            // ISO 8601
  generated_by: string;            // Full name or username
  period: {
    from_date: string;
    to_date: string;
    days: number;
    label: string;                 // e.g. "October 2025"
  };
  sections_denied: string[];       // section_types stripped due to role
  sections: ReportSectionResponse[];
  ai_summary: AiSummary | null;    // null if include_ai_insights was false
}

interface ReportSectionResponse {
  section_type: string;
  title: string;
  config: Record<string, unknown>;
  data: Record<string, unknown>;   // section-specific — see §6 for rendering
  ai_insights?: AiInsights;        // present only when include_ai_insights: true
}

interface AiInsights {
  headline: string;
  summary: string;
  key_observations: string[];
  recommendations: string[];
  cached: boolean;                 // true = returned from cache, no new AI call
}

interface AiSummary {
  executive_headline: string;
  executive_summary: string;
  top_priorities: string[];
  positive_highlights: string[];
  areas_of_concern: string[];
}
```

---

## 4. Role-Based Section Denial

The backend strips any requested sections the user is not allowed to access. The stripped section types appear in `sections_denied`.

**Rules:**
- `sections_denied` is always present (may be empty `[]`).
- Never retry denied sections — the same user will always get the same denial.
- Do not guess why a section was denied — just hide it silently in the PDF and show a banner in the UI.

**Banner pattern:**
```typescript
function SectionDenialBanner({ denied }: { denied: string[] }) {
  if (denied.length === 0) return null;

  return (
    <div className="alert alert-warning" role="alert">
      {denied.length} section{denied.length !== 1 ? 's were' : ' was'} hidden
      because your account does not have access to those modules.
    </div>
  );
}

// Usage — render immediately below the report header
<SectionDenialBanner denied={reportData.sections_denied} />
```

---

## 5. Loading States and Error Handling

### 5.1 Timing Expectations

| AI options | Typical response time |
|---|---|
| `include_ai_insights: false` | 2–8 seconds |
| `include_ai_insights: true` | 10–30 seconds |

Always show a progress indicator. For AI-enabled reports, show a message explaining the delay.

### 5.2 Loading State Component

```typescript
interface GenerateState {
  status: 'idle' | 'loading' | 'success' | 'error';
  data: ReportGenerateResponse | null;
  error: string | null;
}

function useGenerateReport() {
  const [state, setState] = useState<GenerateState>({
    status: 'idle',
    data: null,
    error: null,
  });

  const generate = useCallback(async (request: ReportGenerateRequest) => {
    setState({ status: 'loading', data: null, error: null });

    try {
      const response = await fetch('/api/reports/generate/data/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getAccessToken()}`,
        },
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        throw new Error(errorBody.error || `Server error ${response.status}`);
      }

      const data: ReportGenerateResponse = await response.json();
      setState({ status: 'success', data, error: null });
    } catch (err) {
      setState({
        status: 'error',
        data: null,
        error: err instanceof Error ? err.message : 'Unknown error',
      });
    }
  }, []);

  return { ...state, generate };
}
```

### 5.3 UI Loading Indicator

```tsx
function ReportLoadingOverlay({ aiEnabled }: { aiEnabled: boolean }) {
  return (
    <div className="loading-overlay">
      <Spinner />
      <p className="loading-title">Generating report...</p>
      {aiEnabled && (
        <p className="loading-subtitle">
          AI insights are being generated. This may take up to 30 seconds.
        </p>
      )}
    </div>
  );
}
```

### 5.4 HTTP Error Codes

| Status | Meaning | Handling |
|---|---|---|
| `400` | Bad request (missing `from_date`/`to_date`, invalid section_type) | Show field-level errors from response body |
| `401` | Not authenticated | Redirect to login |
| `403` | Not authorised | Show access denied message |
| `500` | Server-side exception | Show generic error with `error` field from response |

---

## 6. Visual Indicators — Frontend Responsibility

The backend returns **pure numbers only**. All of the following must be computed by the frontend:

### 6.1 Trend Direction

Compare the current period's value against a baseline (e.g., previous period, target). The backend does not include trend direction in the `data` object.

```typescript
type TrendDirection = 'up' | 'down' | 'neutral';

function getTrend(current: number, previous: number | null): TrendDirection {
  if (previous === null || previous === 0) return 'neutral';
  if (current > previous) return 'up';
  if (current < previous) return 'down';
  return 'neutral';
}

function getDeltaPercent(current: number, previous: number | null): string {
  if (previous === null || previous === 0) return '—';
  const delta = ((current - previous) / Math.abs(previous)) * 100;
  const sign = delta >= 0 ? '+' : '';
  return `${sign}${delta.toFixed(1)}%`;
}
```

### 6.2 Color Coding

Define per-metric thresholds. Examples for KEDCO KPIs:

```typescript
type RatingLevel = 'good' | 'warning' | 'critical';

const METRIC_THRESHOLDS: Record<string, { good: number; warning: number }> = {
  hours_of_supply: { good: 16, warning: 10 },   // hours/day
  at_c_loss:       { good: 20, warning: 35 },   // percentage — lower is better
  collection_efficiency: { good: 85, warning: 70 }, // percentage
  system_reliability: { good: 95, warning: 80 }, // percentage
};

function getRating(metric: string, value: number, lowerIsBetter = false): RatingLevel {
  const thresholds = METRIC_THRESHOLDS[metric];
  if (!thresholds) return 'warning';

  if (lowerIsBetter) {
    if (value <= thresholds.good) return 'good';
    if (value <= thresholds.warning) return 'warning';
    return 'critical';
  }

  if (value >= thresholds.good) return 'good';
  if (value >= thresholds.warning) return 'warning';
  return 'critical';
}

const RATING_COLORS: Record<RatingLevel, string> = {
  good: '#22c55e',     // green-500
  warning: '#f59e0b',  // amber-500
  critical: '#ef4444', // red-500
};
```

### 6.3 Metric Cards

```tsx
interface MetricCardProps {
  label: string;
  value: number | string;
  unit?: string;
  rating?: RatingLevel;
  trend?: TrendDirection;
  delta?: string;
}

function MetricCard({ label, value, unit, rating = 'warning', trend, delta }: MetricCardProps) {
  const borderColor = RATING_COLORS[rating];

  return (
    <div className="metric-card" style={{ borderLeft: `4px solid ${borderColor}` }}>
      <span className="metric-label">{label}</span>
      <div className="metric-value-row">
        <span className="metric-value">{value}</span>
        {unit && <span className="metric-unit">{unit}</span>}
        {trend && trend !== 'neutral' && (
          <span className={`trend-arrow trend-${trend}`}>
            {trend === 'up' ? '↑' : '↓'}
          </span>
        )}
      </div>
      {delta && <span className="metric-delta">{delta}</span>}
    </div>
  );
}
```

---

## 7. AI Insights — Display Patterns

### 7.1 Per-Section Insights (Sidebar or Collapsible Panel)

Each section in the response may include an `ai_insights` object when `include_ai_insights: true` was sent. Render these beside or below the section data — never inline with numbers.

```tsx
interface AiInsightsPanelProps {
  insights: AiInsights;
  defaultExpanded?: boolean;
}

function AiInsightsPanel({ insights, defaultExpanded = false }: AiInsightsPanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className="ai-insights-panel">
      <button
        className="ai-insights-toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <SparklesIcon />
        AI Insights
        {insights.cached && (
          <span className="cached-badge" title="Returned from cache">cached</span>
        )}
        <ChevronIcon direction={expanded ? 'up' : 'down'} />
      </button>

      {expanded && (
        <div className="ai-insights-body">
          <p className="ai-headline">{insights.headline}</p>
          <p className="ai-summary">{insights.summary}</p>

          {insights.key_observations.length > 0 && (
            <div className="ai-section">
              <h4>Key Observations</h4>
              <ul>
                {insights.key_observations.map((obs, i) => (
                  <li key={i}>{obs}</li>
                ))}
              </ul>
            </div>
          )}

          {insights.recommendations.length > 0 && (
            <div className="ai-section">
              <h4>Recommendations</h4>
              <ul>
                {insights.recommendations.map((rec, i) => (
                  <li key={i}>{rec}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

### 7.2 Executive AI Summary (Top of Report)

The `ai_summary` field on the root response is the cross-section executive summary. Render it prominently at the top of the report view, above the sections list. In the PDF, include it on its own page immediately after the cover page.

```tsx
function ExecutiveSummaryCard({ summary }: { summary: AiSummary }) {
  return (
    <div className="executive-summary-card">
      <h2 className="executive-headline">{summary.executive_headline}</h2>
      <p className="executive-summary">{summary.executive_summary}</p>

      <div className="summary-columns">
        <div className="summary-column priorities">
          <h3>Top Priorities</h3>
          <ol>
            {summary.top_priorities.map((p, i) => <li key={i}>{p}</li>)}
          </ol>
        </div>

        <div className="summary-column highlights">
          <h3>Positive Highlights</h3>
          <ul>
            {summary.positive_highlights.map((h, i) => <li key={i}>{h}</li>)}
          </ul>
        </div>

        <div className="summary-column concerns">
          <h3>Areas of Concern</h3>
          <ul>
            {summary.areas_of_concern.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      </div>
    </div>
  );
}
```

### 7.3 In the PDF

Embed the executive summary as text blocks in the PDF. `@react-pdf/renderer` cannot render React component trees — you must use its primitive components (`Text`, `View`, `Page`).

```tsx
import { Page, View, Text, StyleSheet } from '@react-pdf/renderer';

const pdfStyles = StyleSheet.create({
  executivePage: { padding: 40, fontFamily: 'Helvetica' },
  headline: { fontSize: 18, fontWeight: 'bold', marginBottom: 12 },
  summaryText: { fontSize: 11, lineHeight: 1.6, marginBottom: 20 },
  sectionTitle: { fontSize: 13, fontWeight: 'bold', marginBottom: 8, color: '#1e40af' },
  bulletItem: { fontSize: 10, lineHeight: 1.5, marginBottom: 4 },
});

function ExecutiveSummaryPage({ summary }: { summary: AiSummary }) {
  return (
    <Page size="A4" style={pdfStyles.executivePage}>
      <Text style={pdfStyles.headline}>{summary.executive_headline}</Text>
      <Text style={pdfStyles.summaryText}>{summary.executive_summary}</Text>

      <Text style={pdfStyles.sectionTitle}>Top Priorities</Text>
      {summary.top_priorities.map((p, i) => (
        <Text key={i} style={pdfStyles.bulletItem}>{i + 1}. {p}</Text>
      ))}

      <Text style={[pdfStyles.sectionTitle, { marginTop: 16 }]}>Positive Highlights</Text>
      {summary.positive_highlights.map((h, i) => (
        <Text key={i} style={pdfStyles.bulletItem}>• {h}</Text>
      ))}

      <Text style={[pdfStyles.sectionTitle, { marginTop: 16 }]}>Areas of Concern</Text>
      {summary.areas_of_concern.map((c, i) => (
        <Text key={i} style={pdfStyles.bulletItem}>• {c}</Text>
      ))}
    </Page>
  );
}
```

---

## 8. Comparison Sections

Comparison sections (`entity_comparison`, `period_comparison`, `customer_comparison`) are first-class section types. Pass them in the `sections` array exactly like any other section. The comparison engine runs server-side and the result is embedded in `section.data`.

### 8.1 Entity Comparison Config

```typescript
// Compare specific feeders/districts/substations across metrics
{
  section_type: "entity_comparison",
  config: {
    entity_type: "feeder",          // "feeder" | "district" | "substation" | "state"
    entity_ids: [1, 2, 3, 4, 5],   // IDs from GET /api/reports/filters/options/
    metrics: ["hours_of_supply", "at_c_loss", "energy_delivered"]
  }
}
```

### 8.2 Customer Comparison Config

```typescript
// Compare customers within a scope (feeder, district, etc.)
{
  section_type: "customer_comparison",
  config: {
    customer_type: "MDI",           // "MDI" | "MSI" | "residential" | "commercial"
    scope_type: "feeder",           // "feeder" | "district" | "substation"
    scope_id: 5                     // ID of the scope entity
  }
}
```

### 8.3 Rendering Comparison Data

The `data` field of a comparison section is a structured array. Render it as a sortable table:

```typescript
// Typical entity_comparison data shape
interface EntityComparisonData {
  entity_type: string;
  metrics: string[];
  entities: Array<{
    id: number;
    name: string;
    values: Record<string, number | null>;
    rank: Record<string, number>;   // rank per metric
  }>;
}

function EntityComparisonTable({ data }: { data: EntityComparisonData }) {
  const [sortMetric, setSortMetric] = useState<string>(data.metrics[0]);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const sorted = [...data.entities].sort((a, b) => {
    const av = a.values[sortMetric] ?? -Infinity;
    const bv = b.values[sortMetric] ?? -Infinity;
    return sortDir === 'desc' ? bv - av : av - bv;
  });

  return (
    <table className="comparison-table">
      <thead>
        <tr>
          <th>Rank</th>
          <th>{data.entity_type}</th>
          {data.metrics.map(m => (
            <th
              key={m}
              className={m === sortMetric ? 'sorted' : ''}
              onClick={() => {
                if (m === sortMetric) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
                else { setSortMetric(m); setSortDir('desc'); }
              }}
            >
              {formatMetricLabel(m)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((entity, index) => (
          <tr key={entity.id}>
            <td>{index + 1}</td>
            <td>{entity.name}</td>
            {data.metrics.map(m => (
              <td
                key={m}
                style={{ color: getRatingColorForMetric(m, entity.values[m]) }}
              >
                {entity.values[m] !== null ? formatMetricValue(m, entity.values[m]!) : '—'}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

---

## 9. PDF Generation with @react-pdf/renderer

### 9.1 Installation

```bash
npm install @react-pdf/renderer
# or
yarn add @react-pdf/renderer
```

### 9.2 Core Pattern — Section-to-PDF Component

Create one PDF component per section type. Each component receives the raw `data` object from the API response.

```tsx
import {
  Document,
  Page,
  View,
  Text,
  Image,
  StyleSheet,
  Font,
  pdf,
} from '@react-pdf/renderer';

// Register fonts once at module level
Font.register({
  family: 'Inter',
  fonts: [
    { src: '/fonts/Inter-Regular.ttf' },
    { src: '/fonts/Inter-Bold.ttf', fontWeight: 'bold' },
  ],
});

const styles = StyleSheet.create({
  page: {
    fontFamily: 'Inter',
    padding: 40,
    fontSize: 10,
    color: '#1f2937',
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: 'bold',
    marginBottom: 12,
    color: '#1e3a5f',
    borderBottom: '1pt solid #e5e7eb',
    paddingBottom: 6,
  },
  metricGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 16,
  },
  metricCard: {
    width: '22%',
    border: '1pt solid #e5e7eb',
    borderRadius: 4,
    padding: 8,
    backgroundColor: '#f9fafb',
  },
  metricLabel: {
    fontSize: 8,
    color: '#6b7280',
    marginBottom: 4,
  },
  metricValue: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#111827',
  },
  metricUnit: {
    fontSize: 8,
    color: '#6b7280',
  },
});
```

### 9.3 PDF Section Components

```tsx
// Cover page PDF component
function CoverPagePDF({
  reportData,
}: {
  reportData: ReportGenerateResponse;
}) {
  return (
    <Page size="A4" style={{ ...styles.page, justifyContent: 'center', alignItems: 'center' }}>
      <Image src="/images/kedco-logo.png" style={{ width: 120, marginBottom: 32 }} />
      <Text style={{ fontSize: 24, fontWeight: 'bold', textAlign: 'center', marginBottom: 8 }}>
        {reportData.report_title}
      </Text>
      {reportData.report_subtitle && (
        <Text style={{ fontSize: 14, color: '#6b7280', textAlign: 'center', marginBottom: 32 }}>
          {reportData.report_subtitle}
        </Text>
      )}
      <Text style={{ fontSize: 11, color: '#374151' }}>
        Period: {reportData.period.label}
      </Text>
      <Text style={{ fontSize: 10, color: '#9ca3af', marginTop: 8 }}>
        Generated by {reportData.generated_by} on{' '}
        {new Date(reportData.generated_at).toLocaleDateString('en-GB', {
          day: 'numeric', month: 'long', year: 'numeric'
        })}
      </Text>
    </Page>
  );
}

// Technical metrics PDF component
function TechnicalMetricsPDF({ data }: { data: Record<string, unknown> }) {
  // data contains the raw metrics returned by the backend
  const metrics = data as {
    hours_of_supply?: number;
    average_load?: number;
    energy_delivered?: number;
    total_interruptions?: number;
    [key: string]: number | undefined;
  };

  const METRIC_DISPLAY: Record<string, { label: string; unit: string }> = {
    hours_of_supply: { label: 'Hours of Supply', unit: 'hrs/day' },
    average_load: { label: 'Average Load', unit: 'MW' },
    energy_delivered: { label: 'Energy Delivered', unit: 'MWh' },
    total_interruptions: { label: 'Total Interruptions', unit: 'count' },
    peak_load: { label: 'Peak Load', unit: 'MW' },
    daily_average_consumption: { label: 'Daily Avg Consumption', unit: 'MWh' },
  };

  return (
    <Page size="A4" style={styles.page}>
      <Text style={styles.sectionTitle}>Technical Metrics</Text>
      <View style={styles.metricGrid}>
        {Object.entries(METRIC_DISPLAY).map(([key, { label, unit }]) => {
          const value = metrics[key];
          if (value === undefined || value === null) return null;
          return (
            <View key={key} style={styles.metricCard}>
              <Text style={styles.metricLabel}>{label}</Text>
              <Text style={styles.metricValue}>
                {typeof value === 'number' ? value.toLocaleString('en-NG', { maximumFractionDigits: 2 }) : value}
              </Text>
              <Text style={styles.metricUnit}>{unit}</Text>
            </View>
          );
        })}
      </View>
    </Page>
  );
}
```

### 9.4 Assembling the Full PDF Document

```tsx
function buildPDFDocument(reportData: ReportGenerateResponse): JSX.Element {
  const pageSize = reportData.orientation === 'landscape' ? 'A4' : 'A4';
  const pageOrientation = reportData.orientation === 'landscape' ? 'landscape' : 'portrait';

  return (
    <Document
      title={reportData.report_title}
      author={reportData.generated_by}
      creator="Raven"
    >
      {reportData.sections.map((section, index) => {
        switch (section.section_type) {
          case 'cover_page':
            return <CoverPagePDF key={index} reportData={reportData} />;

          case 'technical_metrics':
            return (
              <TechnicalMetricsPDF
                key={index}
                data={section.data}
              />
            );

          case 'entity_comparison':
          case 'period_comparison':
          case 'customer_comparison':
            return (
              <ComparisonSectionPDF
                key={index}
                sectionType={section.section_type}
                data={section.data}
              />
            );

          // Add cases for each section type you support
          default:
            return null;
        }
      })}
    </Document>
  );
}
```

### 9.5 Triggering the Download

```typescript
async function downloadReportPDF(reportData: ReportGenerateResponse): Promise<void> {
  const document = buildPDFDocument(reportData);

  // pdf() returns a Blob
  const blob = await pdf(document).toBlob();

  const url = URL.createObjectURL(blob);
  const link = window.document.createElement('a');
  link.href = url;
  link.download = `${reportData.report_title.replace(/\s+/g, '_')}.pdf`;
  link.click();

  // Clean up
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
```

### 9.6 Inline PDF Preview

```tsx
import { PDFViewer } from '@react-pdf/renderer';

function ReportPDFPreview({ reportData }: { reportData: ReportGenerateResponse }) {
  return (
    <PDFViewer width="100%" height="800px">
      {buildPDFDocument(reportData)}
    </PDFViewer>
  );
}
```

Note: `PDFViewer` renders an iframe. Do not use it server-side (SSR). Gate it with a `typeof window !== 'undefined'` check if your app uses SSR.

---

## 10. Report Builder UI — Recommended Flow

```
1. Load filter options        GET /api/reports/filters/options/
2. Load available sections    GET /api/reports/sections/available/
3. Load saved templates       GET /api/reports/templates/
4. User builds report:
     a. Set title, subtitle, orientation
     b. Set date range and optional filters
     c. Add sections from the section picker
     d. Configure each section (entity IDs for comparisons, etc.)
     e. Toggle AI insights on/off
5. (Optional) Preview single section:
     POST /api/reports/preview/section/
6. Generate report:
     POST /api/reports/generate/data/
7. Display results:
     a. Render sections in UI
     b. Show denial banner if sections_denied is non-empty
     c. Show AI summary at top
     d. Show per-section AI insights in collapsible panels
8. User clicks "Download PDF":
     buildPDFDocument() → pdf().toBlob() → download
9. (Optional) Save as template:
     POST /api/reports/templates/
```

---

## 11. TypeScript Types Reference

```typescript
// Place in src/types/reports.ts

export type ReportOrientation = 'portrait' | 'landscape';
export type RatingLevel = 'good' | 'warning' | 'critical';
export type TrendDirection = 'up' | 'down' | 'neutral';

export interface ReportFilters {
  from_date: string;
  to_date: string;
  states?: number[];
  districts?: number[];
  feeders?: number[];
  substations?: number[];
  bands?: number[];
}

export interface ReportSectionInput {
  section_type: string;
  title?: string;
  config?: Record<string, unknown>;
}

export interface ReportGenerateRequest {
  report_title: string;
  report_subtitle?: string;
  orientation: ReportOrientation;
  company_name?: string;
  sections: ReportSectionInput[];
  filters: ReportFilters;
  include_ai_insights?: boolean;
  include_ai_summary?: boolean;
}

export interface AiInsights {
  headline: string;
  summary: string;
  key_observations: string[];
  recommendations: string[];
  cached: boolean;
}

export interface AiSummary {
  executive_headline: string;
  executive_summary: string;
  top_priorities: string[];
  positive_highlights: string[];
  areas_of_concern: string[];
}

export interface ReportPeriod {
  from_date: string;
  to_date: string;
  days: number;
  label: string;
}

export interface ReportSectionResponse {
  section_type: string;
  title: string;
  config: Record<string, unknown>;
  data: Record<string, unknown>;
  ai_insights?: AiInsights;
}

export interface ReportGenerateResponse {
  report_id: string;
  report_title: string;
  report_subtitle: string;
  orientation: ReportOrientation;
  company_name: string;
  generated_at: string;
  generated_by: string;
  period: ReportPeriod;
  sections_denied: string[];
  sections: ReportSectionResponse[];
  ai_summary: AiSummary | null;
}

export interface AvailableSection {
  section_type: string;
  display_name: string;
  description: string;
  category: string;
  supports_chart: boolean;
  config_options: Record<string, unknown>;
}

export interface FilterOptions {
  states: Array<{ id: number; name: string }>;
  districts: Array<{ id: number; name: string; state__name: string; state_id: number }>;
  substations: Array<{ id: number; name: string }>;
  bands: Array<{ id: number; name: string; description: string }>;
  feeders: Array<{
    id: number;
    name: string;
    band__name: string;
    substation__name: string;
    business_district__name: string;
    substation_id: number;
    business_district_id: number;
    band_id: number;
    voltage_level: string;
  }>;
  voltage_levels: Array<{ id: string; name: string }>;
}

export interface GeneratedReportHistory {
  id: string;
  report_title: string;
  category: string;
  filters_used: ReportFilters;
  sections_included: string[];
  generation_method: 'pdf' | 'data';
  generated_at: string;
}
```

---

## 12. Complete Working Example

```tsx
// src/pages/ReportGeneratorPage.tsx
import { useState, useEffect } from 'react';
import { pdf } from '@react-pdf/renderer';
import type {
  ReportGenerateRequest,
  ReportGenerateResponse,
  FilterOptions,
} from '../types/reports';
import { buildPDFDocument } from '../components/reports/pdf/ReportDocument';
import { ExecutiveSummaryCard } from '../components/reports/ExecutiveSummaryCard';
import { SectionDenialBanner } from '../components/reports/SectionDenialBanner';
import { ReportSectionRenderer } from '../components/reports/ReportSectionRenderer';
import { AiInsightsPanel } from '../components/reports/AiInsightsPanel';

const API_BASE = '/api/reports';

export default function ReportGeneratorPage() {
  const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
  const [reportData, setReportData] = useState<ReportGenerateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pdfGenerating, setPdfGenerating] = useState(false);

  // Form state
  const [fromDate, setFromDate] = useState('2025-10-01');
  const [toDate, setToDate] = useState('2025-10-31');
  const [includeAI, setIncludeAI] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/filters/options/`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` },
    })
      .then(r => r.json())
      .then(setFilterOptions)
      .catch(console.error);
  }, []);

  async function handleGenerate() {
    setLoading(true);
    setError(null);
    setReportData(null);

    const request: ReportGenerateRequest = {
      report_title: 'October 2025 Performance Report',
      report_subtitle: 'Kano Electricity Distribution Company',
      orientation: 'portrait',
      company_name: 'KANO ELECTRICITY DISTRIBUTION COMPANY',
      sections: [
        { section_type: 'cover_page', config: {} },
        { section_type: 'technical_metrics', config: {} },
        {
          section_type: 'entity_comparison',
          config: {
            entity_type: 'feeder',
            entity_ids: filterOptions?.feeders.slice(0, 5).map(f => f.id) ?? [],
            metrics: ['hours_of_supply', 'at_c_loss'],
          },
        },
      ],
      filters: {
        from_date: fromDate,
        to_date: toDate,
      },
      include_ai_insights: includeAI,
      include_ai_summary: includeAI,
    };

    try {
      const res = await fetch(`${API_BASE}/generate/data/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('access_token')}`,
        },
        body: JSON.stringify(request),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }

      const data: ReportGenerateResponse = await res.json();
      setReportData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate report');
    } finally {
      setLoading(false);
    }
  }

  async function handleDownloadPDF() {
    if (!reportData) return;
    setPdfGenerating(true);
    try {
      const doc = buildPDFDocument(reportData);
      const blob = await pdf(doc).toBlob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${reportData.report_title.replace(/\s+/g, '_')}.pdf`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } finally {
      setPdfGenerating(false);
    }
  }

  return (
    <div className="report-generator">
      {/* Form */}
      <div className="report-controls">
        <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} />
        <input type="date" value={toDate} onChange={e => setToDate(e.target.value)} />
        <label>
          <input
            type="checkbox"
            checked={includeAI}
            onChange={e => setIncludeAI(e.target.checked)}
          />
          Include AI insights (adds 10–30 seconds)
        </label>
        <button onClick={handleGenerate} disabled={loading}>
          {loading ? 'Generating...' : 'Generate Report'}
        </button>
      </div>

      {/* Loading */}
      {loading && (
        <div className="loading-overlay">
          <p>Generating report{includeAI ? ' with AI insights' : ''}...</p>
          {includeAI && <p>This may take up to 30 seconds.</p>}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="error-banner" role="alert">
          Error: {error}
        </div>
      )}

      {/* Results */}
      {reportData && (
        <div className="report-results">
          <div className="report-actions">
            <h1>{reportData.report_title}</h1>
            <p>Period: {reportData.period.label} · Generated by {reportData.generated_by}</p>
            <button onClick={handleDownloadPDF} disabled={pdfGenerating}>
              {pdfGenerating ? 'Building PDF...' : 'Download PDF'}
            </button>
          </div>

          {/* Denied sections banner */}
          <SectionDenialBanner denied={reportData.sections_denied} />

          {/* Executive AI summary */}
          {reportData.ai_summary && (
            <ExecutiveSummaryCard summary={reportData.ai_summary} />
          )}

          {/* Sections */}
          {reportData.sections.map((section, i) => (
            <div key={i} className="report-section">
              <ReportSectionRenderer section={section} period={reportData.period} />
              {section.ai_insights && (
                <AiInsightsPanel insights={section.ai_insights} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

---

## Quick Reference

| Task | How |
|---|---|
| Get available sections for current user | `GET /api/reports/sections/available/` |
| Get filter dropdowns | `GET /api/reports/filters/options/` |
| Generate report data | `POST /api/reports/generate/data/` |
| View past reports | `GET /api/reports/history/` |
| Save a template | `POST /api/reports/templates/` |
| Enable AI insights | Set `include_ai_insights: true` in request |
| Handle denied sections | Check `response.sections_denied`, show banner |
| Generate PDF | `pdf(buildPDFDocument(reportData)).toBlob()` |
| Compute trend arrows | Frontend only — backend returns raw numbers |
| Compute color coding | Frontend only — use per-metric thresholds |
| Comparison sections | Pass in `sections[]` like any other section; backend handles the data |
