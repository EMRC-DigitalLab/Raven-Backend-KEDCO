# Raven API Documentation

Each model uses a UUID as the primary key.

---

## Core

### 1. **Band**

- **Endpoint:** `/api/common/bands/`
- **Fields:** `id`, `name`, `description`, `slug (auto)`
- **CRUD:**

  - `GET`: List all bands
  - `POST`: Create a new band
  - `GET /<id>/`: Retrieve a specific band
  - `PUT /<id>/`: Update a band
  - `DELETE /<id>/`: Delete a band

### 2. **State**

- **Endpoint:** `/api/common/states/`
- **Fields:** `id`, `name`, `slug`

### 3. **BusinessDistrict**

- **Endpoint:** `/api/common/business-districts/`
- **Fields:** `id`, `name`, `state`, `slug`

### 4. **InjectionSubstation**

- **Endpoint:** `/api/common/injection-substations/`
- **Fields:** `id`, `name`, `slug`

### 5. **Feeder**

- **Endpoint:** `/api/common/feeders/`
- **Fields:** `id`, `name`, `band`, `voltage_level`, `substation`, `business_district`, `slug`

### 6. **DistributionTransformer**

- **Endpoint:** `/api/common/transformers/`
- **Fields:** `id`, `name`, `feeder`, `slug`

---

## Commercial Endpoints

### 1. **Customer**

- **Endpoint:** `/api/commercial/customers/`
- **Fields:** `name`, `category`, `metering_type`, `band`, `transformer`, `joined_date`

### 3. **DailyRevenueCollected**

- **Endpoint:** `/api/commercial/daily-revenue-collected/`
- **Fields:** `feeder`, `date`, `amount`

### 4. **MonthlyRevenueBilled**

- **Endpoint:** `/api/commercial/monthly-revenue-billed/`
- **Fields:** `feeder`, `month`, `amount`

### 5. **MonthlyEnergyBilled**

- **Endpoint:** `/api/commercial/monthly-energy-billed/`
- **Fields:** `feeder`, `month`, `energy_mwh`

### 6. **MonthlyCustomerStats**

- **Endpoint:** `/api/commercial/monthly-customer-stats/`
- **Fields:** `feeder`, `month`, `customer_count`, `customers_billed`, `customer_response_count`

### 7. **SalesRepresentative**

- **Endpoint:** `/api/commercial/sales-representatives/`
- **Fields:** `name`, `assigned_transformers`, `slug`

### 8. **SalesRepPerformance**

- **Endpoint:** `/api/commercial/sales-rep-performance/`
- **Fields:** `sales_rep`, `month`, `outstanding_billed`, `current_billed`, `collections`, `daily_run_rate`, `collections_on_outstanding`, `active_accounts`, `suspended_accounts`

### 9. **DailyCollection**

- **Endpoint:** `/api/commercial/daily-collections/`
- **Fields:** `sales_rep`, `date`, `amount`, `collection_type`, `vendor_name`

### 10. **MonthlyCommercialSummary**

- **Endpoint:** `/api/commercial/monthly-commercial-summary/`
- **Fields:** `sales_rep`, `month`, `customers_billed`, `customers_responded`, `revenue_billed`, `revenue_collected`

---

## Endpoints

### 1. **ExpenseCategory**

- **Endpoint:** `/api/financial/expense-categories/`
- **Fields:** `name`, `is_special`

### 2. **GLBreakdown**

- **Endpoint:** `/api/financial/gl-breakdowns/`
- **Fields:** `name`

### 3. **Expense**

- **Endpoint:** `/api/financial/expenses/`
- **Fields:** `district`, `date`, `purpose`, `payee`, `gl_account_number`, `gl_breakdown`, `opex_category`, `debit`, `credit`, `created_at`

### 4. **MonthlyRevenueBilled**

- **Endpoint:** `/api/financial/monthly-revenue-billed/`
- **Fields:** `feeder`, `month`, `amount`, `created_at`

---

## HR Endpoints

### 1. **Department**

- **Endpoint:** `/api/hr/departments/`
- **Fields:** `name`, `slug`

### 2. **Role**

- **Endpoint:** `/api/hr/roles/`
- **Fields:** `title`, `department`, `slug`

### 3. **Staff**

- **Endpoint:** `/api/hr/staff/`
- **Fields:** `full_name`, `email`, `phone_number`, `gender`, `birth_date`, `salary`, `hire_date`, `exit_date`, `grade`, `role`, `department`, `state`, `district`

---

## Technical Endpoints

### 1. **EnergyDelivered**

- **Endpoint:** `/api/technical/energy-delivered/`
- **Fields:** `feeder`, `date`, `energy_mwh`

### 2. **HourlyLoad**

- **Endpoint:** `/api/technical/hourly-loads/`
- **Fields:** `feeder`, `date`, `hour`, `load_mw`

### 3. **FeederInterruption**

- **Endpoint:** `/api/technical/feeder-interruptions/`
- **Fields:** `feeder`, `interruption_type`, `description`, `occurred_at`, `restored_at`, `duration_hours (computed)`

### 4. **DailyHoursOfSupply**

- **Endpoint:** `/api/technical/daily-hours-of-supply/`
- **Fields:** `feeder`, `date`, `hours_supplied`

---

## Notes

- All models use a UUID primary key by default (`UUIDModel`)
- Slugs are auto-generated from names when applicable
- `unique_together` constraints ensure no duplication of time-series data
