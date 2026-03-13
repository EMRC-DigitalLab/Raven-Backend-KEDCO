# hr/utils/kpi_calculator.py - COMPLETE FIXED VERSION
"""
Executive KPI Auto-Calculator Service
Calculates KPI values from existing data using SQL queries for performance
Follows same patterns as technical views (all_feeders.py, overview_views.py)
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta
from django.db import connection
from django.db.models import Avg, Count, F, Max, Min, Q, Sum
from django.utils import timezone

from commercial.models import (
    Customer,
    DailyCollection,
    MonthlyCommercialSummary,
    MonthlyEnergyBilled,
    MonthlyRevenueBilled,
)
from common.models import Band, BusinessDistrict, Feeder, State
from financial.models import HQOpex, MOInvoice, NBETInvoice, Opex, SalaryPayment
from hr.models import Staff
from technical.constants import TURNAROUND_EXCLUSIONS
from technical.models import (
    FeederEnergyDaily,
    FeederEnergyMonthly,
    FeederInterruption,
    HourlyLoad,
)


class KPICalculationService:
    """
    Base service class for calculating executive KPIs from existing data
    """
    
    @staticmethod
    def round_decimal(value, places=2):
        """Round decimal to specified places"""
        try:
            if value is None:
                return Decimal('0')
            return Decimal(str(value)).quantize(
                Decimal(10) ** -places, 
                rounding=ROUND_HALF_UP
            )
        except:
            return Decimal('0')
    
    @staticmethod
    def get_period_range(period_date, period_type='monthly'):
        """
        Get date range for a given period
        Returns (start_date, end_date) tuple
        """
        if period_type == 'monthly':
            start_date = period_date.replace(day=1)
            if start_date.month == 12:
                end_date = date(start_date.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(start_date.year, start_date.month + 1, 1) - timedelta(days=1)
        elif period_type == 'quarterly':
            quarter = (period_date.month - 1) // 3
            start_date = date(period_date.year, quarter * 3 + 1, 1)
            end_date = start_date + relativedelta(months=3) - timedelta(days=1)
        elif period_type == 'annually':
            start_date = date(period_date.year, 1, 1)
            end_date = date(period_date.year, 12, 31)
        else:  # daily
            start_date = period_date
            end_date = period_date
        
        return start_date, end_date


# =============================================================================
# CTO KPI CALCULATORS - USING SQL QUERIES LIKE TECHNICAL VIEWS
# =============================================================================

class CTOKPICalculator(KPICalculationService):
    """Calculator for Chief Technology Officer KPIs - Using SQL for performance"""
    
    @classmethod
    def calculate_energy_delivered(cls, period_date, period_type='monthly', 
                                   state=None, district=None, feeder=None):
        """
        Calculate total energy delivered in MWh (NOT GWh - frontend uses MWh)
        KPI: Energy Delivered (MWh)
        Uses SQL query like technical views
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Build location filter
        location_filter = ""
        params = [start_date, end_date]
        
        if feeder:
            location_filter = "AND hl.feeder_id = %s"
            params.append(feeder.id)
        elif district:
            location_filter = """
                AND hl.feeder_id IN (
                    SELECT id FROM common_feeder 
                    WHERE business_district_id = %s AND is_onboarded = TRUE
                )
            """
            params.append(district.id)
        elif state:
            location_filter = """
                AND hl.feeder_id IN (
                    SELECT f.id FROM common_feeder f
                    INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
                    WHERE bd.state_id = %s AND f.is_onboarded = TRUE
                )
            """
            params.append(state.id)
        else:
            location_filter = """
                AND hl.feeder_id IN (
                    SELECT id FROM common_feeder WHERE is_onboarded = TRUE
                )
            """
        
        # SQL query to sum energy (load_mw × hours)
        query = f"""
            SELECT COALESCE(SUM(hl.load_mw), 0) as total_energy_mwh
            FROM technical_hourlyload hl
            WHERE hl.date BETWEEN %s AND %s
                AND hl.load_mw > 0
                {location_filter}
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchone()
            energy_mwh = result[0] if result else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(energy_mwh, 2)),
            'unit': 'MWh',
            'calculation_method': 'sum_hourly_load_from_hourlyload',
            'source': 'HourlyLoad',
            'calculated_at': timezone.now().isoformat()
        }
    
    @classmethod
    def calculate_avg_hours_of_supply(cls, period_date, period_type='monthly',
                                      state=None, district=None, feeder=None):
        """
        Calculate average hours of supply per day
        KPI: Average Hours of Supply per Day
        Uses SQL query like calculate_feeder_hours_of_supply_sql
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        period_days = (end_date - start_date).days + 1
        
        # Build location filter
        location_filter = ""
        params = [start_date, end_date]
        
        if feeder:
            location_filter = "AND feeder_id = %s"
            params.append(feeder.id)
        elif district:
            location_filter = """
                AND feeder_id IN (
                    SELECT id FROM common_feeder 
                    WHERE business_district_id = %s AND is_onboarded = TRUE
                )
            """
            params.append(district.id)
        elif state:
            location_filter = """
                AND feeder_id IN (
                    SELECT f.id FROM common_feeder f
                    INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
                    WHERE bd.state_id = %s AND f.is_onboarded = TRUE
                )
            """
            params.append(state.id)
        else:
            location_filter = """
                AND feeder_id IN (
                    SELECT id FROM common_feeder WHERE is_onboarded = TRUE
                )
            """
        
        # SQL query - average daily supply hours across all feeders
        query = f"""
            SELECT 
                AVG(daily_hours) as avg_hours
            FROM (
                SELECT 
                    feeder_id,
                    date,
                    COUNT(DISTINCT hour) as daily_hours
                FROM technical_hourlyload
                WHERE date BETWEEN %s AND %s
                    AND load_mw > 0
                    {location_filter}
                GROUP BY feeder_id, date
            ) daily_supply
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchone()
            avg_hours = result[0] if result and result[0] else 0
        
        return {
            'value': float(cls.round_decimal(avg_hours, 2)),
            'unit': 'hours/day',
            'calculation_method': 'average_daily_supply_hours_from_hourlyload',
            'source': 'HourlyLoad',
            'calculated_at': timezone.now().isoformat()
        }
    
    @classmethod
    def calculate_grid_offtake_capacity(cls, period_date, period_type='monthly',
                                       state=None, district=None):
        """
        Calculate maximum grid offtake capacity
        KPI: Grid Offtake Capacity (MW)
        
        FIXED: Uses peak coincident demand - the maximum total load across all feeders
        at the same hour (not individual feeder peaks at different times)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Build location filter
        location_filter = ""
        params = [start_date, end_date]
        
        if district:
            location_filter = """
                AND feeder_id IN (
                    SELECT id FROM common_feeder 
                    WHERE business_district_id = %s AND is_onboarded = TRUE
                )
            """
            params.append(district.id)
        elif state:
            location_filter = """
                AND feeder_id IN (
                    SELECT f.id FROM common_feeder f
                    INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
                    WHERE bd.state_id = %s AND f.is_onboarded = TRUE
                )
            """
            params.append(state.id)
        else:
            location_filter = """
                AND feeder_id IN (
                    SELECT id FROM common_feeder WHERE is_onboarded = TRUE
                )
            """
        
        # SQL query for peak coincident demand (max total load at any single hour)
        query = f"""
            SELECT COALESCE(MAX(hourly_total), 0) as max_peak
            FROM (
                SELECT date, hour, SUM(load_mw) as hourly_total
                FROM technical_hourlyload
                WHERE date BETWEEN %s AND %s
                    {location_filter}
                GROUP BY date, hour
            ) hourly_sums
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchone()
            max_peak = result[0] if result else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(max_peak, 2)),
            'unit': 'MW',
            'calculation_method': 'peak_coincident_demand_max_total_at_single_hour',
            'source': 'HourlyLoad',
            'calculated_at': timezone.now().isoformat()
        }
    
    @classmethod
    def calculate_sla_compliance(cls, period_date, period_type='monthly',
                                state=None, district=None):
        """
        Calculate SLA compliance percentage
        KPI: SLA Compliance (%)
        
        SLA targets by band:
        - Band A: 20 hours/day
        - Band B: 16 hours/day
        - Band C: 12 hours/day
        - Band D: 8 hours/day
        - Band E: 4 hours/day
        
        Compliance = (Feeders meeting target / Total feeders) × 100
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get onboarded feeders with their bands
        feeders_query = Feeder.objects.filter(is_onboarded=True).select_related('band')
        
        if district:
            feeders_query = feeders_query.filter(business_district=district)
        elif state:
            feeders_query = feeders_query.filter(business_district__state=state)
        
        feeders = list(feeders_query.values('id', 'band__name'))
        
        if not feeders:
            return {
                'value': 0,
                'unit': '%',
                'calculation_method': 'feeders_meeting_band_target / total_feeders * 100',
                'source': 'HourlyLoad, Feeder',
                'metadata': {'total_feeders': 0, 'compliant_feeders': 0},
                'calculated_at': timezone.now().isoformat()
            }
        
        # Band target mapping
        band_targets = {
            'A': 20,
            'B': 16,
            'C': 12,
            'D': 8,
            'E': 4
        }
        
        # Calculate average supply hours per feeder
        feeder_ids = [f['id'] for f in feeders]
        
        query = """
            SELECT 
                feeder_id,
                AVG(daily_hours) as avg_hours
            FROM (
                SELECT 
                    feeder_id,
                    date,
                    COUNT(DISTINCT hour) as daily_hours
                FROM technical_hourlyload
                WHERE date BETWEEN %s AND %s
                    AND load_mw > 0
                    AND feeder_id = ANY(%s)
                GROUP BY feeder_id, date
            ) daily_supply
            GROUP BY feeder_id
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [start_date, end_date, feeder_ids])
            results = cursor.fetchall()
        
        # Map feeder supply hours
        feeder_hours = {row[0]: float(row[1]) for row in results}
        
        # Count compliant feeders
        compliant_count = 0
        for feeder in feeders:
            feeder_id = feeder['id']
            band_name = feeder['band__name']
            
            # Get target for this band (default to 0 if band not found)
            target = band_targets.get(band_name, 0)
            
            # Check if feeder meets target
            avg_hours = feeder_hours.get(feeder_id, 0)
            if avg_hours >= target:
                compliant_count += 1
        
        # Calculate compliance percentage
        compliance = (compliant_count / len(feeders) * 100) if feeders else 0
        
        return {
            'value': float(cls.round_decimal(compliance, 2)),
            'unit': '%',
            'calculation_method': 'feeders_meeting_band_target / total_feeders * 100',
            'source': 'HourlyLoad, Feeder',
            'metadata': {
                'total_feeders': len(feeders),
                'compliant_feeders': compliant_count,
                'band_targets': band_targets
            },
            'calculated_at': timezone.now().isoformat()
        }
    
    @classmethod
    def calculate_system_availability(cls, period_date, period_type='monthly',
                                     state=None, district=None):
        """
        Calculate system availability percentage
        KPI: System Availability (%)
        
        Formula: (Total possible hours - Total interruption hours) / Total possible hours × 100
        
        Only counts interruptions that OCCURRED in the period (not carried over)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        period_days = (end_date - start_date).days + 1
        
        # Get onboarded feeders
        feeders_query = Feeder.objects.filter(is_onboarded=True)
        
        if district:
            feeders_query = feeders_query.filter(business_district=district)
        elif state:
            feeders_query = feeders_query.filter(business_district__state=state)
        
        feeder_count = feeders_query.count()
        
        if feeder_count == 0:
            return {
                'value': 0,
                'unit': '%',
                'calculation_method': '(total_hours - interruption_hours) / total_hours * 100',
                'source': 'FeederInterruption',
                'metadata': {'feeder_count': 0},
                'calculated_at': timezone.now().isoformat()
            }
        
        # Total possible hours
        total_possible_hours = feeder_count * period_days * 24
        
        # Get feeder IDs
        feeder_ids = list(feeders_query.values_list('id', flat=True))
        
        # Calculate total interruption hours (ONLY interruptions that occurred in period)
        # Use timezone conversion for date comparison
        now = timezone.now()
        
        query = """
            SELECT 
                COALESCE(SUM(
                    GREATEST(
                        EXTRACT(EPOCH FROM (
                            LEAST(COALESCE(restored_at, %s), %s) - occurred_at
                        )) / 3600.0,
                        0
                    )
                ), 0) as total_hours
            FROM technical_feederinterruption
            WHERE feeder_id = ANY(%s)
                AND (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [now, now, feeder_ids, start_date, end_date])
            result = cursor.fetchone()
            total_interruption_hours = float(result[0]) if result else 0
        
        # Calculate availability
        availability = ((total_possible_hours - total_interruption_hours) / total_possible_hours * 100) if total_possible_hours > 0 else 0
        
        return {
            'value': float(cls.round_decimal(availability, 2)),
            'unit': '%',
            'calculation_method': '(total_hours - interruption_hours) / total_hours * 100',
            'source': 'FeederInterruption',
            'metadata': {
                'feeder_count': feeder_count,
                'period_days': period_days,
                'total_possible_hours': total_possible_hours,
                'total_interruption_hours': round(total_interruption_hours, 2)
            },
            'calculated_at': timezone.now().isoformat()
        }
    
    @classmethod
    def calculate_avg_interruption_duration(cls, period_date, period_type='monthly',
                                           state=None, district=None, feeder=None):
        """
        Calculate average interruption duration per day
        KPI: Average Interruption Duration (hours/day)
        
        ONLY counts interruptions that OCCURRED in the period
        Uses same logic as calculate_feeder_interruption_metrics_sql
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        period_days = (end_date - start_date).days + 1
        
        # Get onboarded feeders
        feeders_query = Feeder.objects.filter(is_onboarded=True)
        
        if feeder:
            feeders_query = feeders_query.filter(id=feeder.id)
        elif district:
            feeders_query = feeders_query.filter(business_district=district)
        elif state:
            feeders_query = feeders_query.filter(business_district__state=state)
        
        feeder_ids = list(feeders_query.values_list('id', flat=True))
        feeder_count = len(feeder_ids)
        
        if feeder_count == 0:
            return {
                'value': 0,
                'unit': 'hours/day',
                'calculation_method': 'total_interruption_hours / (feeders * days)',
                'source': 'FeederInterruption',
                'metadata': {'feeder_count': 0},
                'calculated_at': timezone.now().isoformat()
            }
        
        # Calculate total interruption hours (ONLY occurred in period)
        now = timezone.now()
        
        query = """
            SELECT 
                COALESCE(SUM(
                    GREATEST(
                        EXTRACT(EPOCH FROM (
                            LEAST(COALESCE(restored_at, %s), %s) - occurred_at
                        )) / 3600.0,
                        0
                    )
                ), 0) as total_hours
            FROM technical_feederinterruption
            WHERE feeder_id = ANY(%s)
                AND (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [now, now, feeder_ids, start_date, end_date])
            result = cursor.fetchone()
            total_hours = float(result[0]) if result else 0
        
        # Calculate average per feeder per day
        avg_per_day = total_hours / (feeder_count * period_days) if (feeder_count * period_days) > 0 else 0
        
        return {
            'value': float(cls.round_decimal(avg_per_day, 2)),
            'unit': 'hours/day',
            'calculation_method': 'total_interruption_hours / (feeders * days)',
            'source': 'FeederInterruption',
            'metadata': {
                'total_interruption_hours': round(total_hours, 2),
                'feeder_count': feeder_count,
                'period_days': period_days
            },
            'calculated_at': timezone.now().isoformat()
        }
    
    @classmethod
    def calculate_saifi(cls, period_date, period_type='monthly',
                       state=None, district=None):
        """
        Calculate SAIFI (System Average Interruption Frequency Index)
        KPI: SAIFI (interruptions/feeder)
        
        FEEDER-BASED METRIC (no customer population available)
        Formula: Total interruption count / Total feeders
        
        ONLY counts interruptions that OCCURRED in the period
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get onboarded feeders
        feeders_query = Feeder.objects.filter(is_onboarded=True)
        
        if district:
            feeders_query = feeders_query.filter(business_district=district)
        elif state:
            feeders_query = feeders_query.filter(business_district__state=state)
        
        feeder_ids = list(feeders_query.values_list('id', flat=True))
        feeder_count = len(feeder_ids)
        
        if feeder_count == 0:
            return {
                'value': 0,
                'unit': 'interruptions/feeder',
                'calculation_method': 'total_interruptions / total_feeders',
                'source': 'FeederInterruption',
                'metadata': {'feeder_count': 0, 'interruption_count': 0},
                'calculated_at': timezone.now().isoformat()
            }
        
        # Count interruptions that OCCURRED in period (timezone conversion)
        query = """
            SELECT COUNT(*) as interruption_count
            FROM technical_feederinterruption
            WHERE feeder_id = ANY(%s)
                AND (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [feeder_ids, start_date, end_date])
            result = cursor.fetchone()
            interruption_count = result[0] if result else 0
        
        # Calculate SAIFI
        saifi = interruption_count / feeder_count if feeder_count > 0 else 0
        
        return {
            'value': float(cls.round_decimal(saifi, 2)),
            'unit': 'interruptions/feeder',
            'calculation_method': 'total_interruptions / total_feeders',
            'source': 'FeederInterruption',
            'metadata': {
                'interruption_count': interruption_count,
                'feeder_count': feeder_count,
                'note': 'Feeder-based metric (no customer population data available)'
            },
            'calculated_at': timezone.now().isoformat()
        }
    
    @classmethod
    def calculate_saidi(cls, period_date, period_type='monthly',
                       state=None, district=None):
        """
        Calculate SAIDI (System Average Interruption Duration Index)
        KPI: SAIDI (minutes/feeder)
        
        FEEDER-BASED METRIC (no customer population available)
        Formula: (Total interruption hours / Total feeders) × 60
        
        ONLY counts interruptions that OCCURRED in the period
        Result in MINUTES
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get onboarded feeders
        feeders_query = Feeder.objects.filter(is_onboarded=True)
        
        if district:
            feeders_query = feeders_query.filter(business_district=district)
        elif state:
            feeders_query = feeders_query.filter(business_district__state=state)
        
        feeder_ids = list(feeders_query.values_list('id', flat=True))
        feeder_count = len(feeder_ids)
        
        if feeder_count == 0:
            return {
                'value': 0,
                'unit': 'minutes/feeder',
                'calculation_method': '(total_interruption_hours / total_feeders) * 60',
                'source': 'FeederInterruption',
                'metadata': {'feeder_count': 0},
                'calculated_at': timezone.now().isoformat()
            }
        
        # Calculate total interruption hours (ONLY occurred in period)
        now = timezone.now()
        
        query = """
            SELECT 
                COALESCE(SUM(
                    GREATEST(
                        EXTRACT(EPOCH FROM (
                            LEAST(COALESCE(restored_at, %s), %s) - occurred_at
                        )) / 3600.0,
                        0
                    )
                ), 0) as total_hours
            FROM technical_feederinterruption
            WHERE feeder_id = ANY(%s)
                AND (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [now, now, feeder_ids, start_date, end_date])
            result = cursor.fetchone()
            total_hours = float(result[0]) if result else 0
        
        # Calculate SAIDI in minutes per feeder
        saidi_minutes = (total_hours / feeder_count * 60) if feeder_count > 0 else 0
        
        return {
            'value': float(cls.round_decimal(saidi_minutes, 2)),
            'unit': 'minutes/feeder',
            'calculation_method': '(total_interruption_hours / total_feeders) * 60',
            'source': 'FeederInterruption',
            'metadata': {
                'total_interruption_hours': round(total_hours, 2),
                'feeder_count': feeder_count,
                'note': 'Feeder-based metric (no customer population data available)'
            },
            'calculated_at': timezone.now().isoformat()
        }


# =============================================================================
# CCO, CFO, CHRO CALCULATORS - KEEP EXISTING LOGIC WITH MINOR FIXES
# =============================================================================

class CCOKPICalculator(KPICalculationService):
    """Calculator for Chief Commercial Officer KPIs"""
    
    @classmethod
    def calculate_billing_efficiency_by_metering_type(cls, period_date, metering_type,
                                                       state=None, district=None):
        """
        Calculate billing efficiency for specific metering type
        KPI: MD Industrial/Non-Industrial/Regions Billing Efficiency (%)
        
        metering_type: 'MD1', 'MD2', 'Non-MD'
        """
        start_date = period_date.replace(day=1)
        end_date = start_date + relativedelta(months=1) - timedelta(days=1)
        
        # Get transformers with customers of this metering type
        transformers_query = Customer.objects.filter(
            metering_type=metering_type
        )
        
        if district:
            transformers_query = transformers_query.filter(transformer__feeder__business_district=district)
        elif state:
            transformers_query = transformers_query.filter(transformer__feeder__business_district__state=state)
        
        transformer_ids = list(transformers_query.values_list('transformer_id', flat=True).distinct())
        
        if not transformer_ids:
            return {
                'value': 0,
                'unit': '%',
                'calculation_method': 'energy_billed / energy_delivered * 100',
                'source': 'MonthlyEnergyBilled, FeederEnergyMonthly',
                'metadata': {'metering_type': metering_type, 'transformer_count': 0}
            }
        
        # Get feeder IDs from transformers
        from common.models import DistributionTransformer
        feeder_ids = list(DistributionTransformer.objects.filter(
            id__in=transformer_ids
        ).values_list('feeder_id', flat=True).distinct())
        
        # Get energy delivered for these feeders
        energy_delivered = FeederEnergyMonthly.objects.filter(
            feeder_id__in=feeder_ids,
            period=start_date
        ).aggregate(Sum('energy_mwh'))['energy_mwh__sum'] or Decimal('0')
        
        # Fallback to daily if no monthly data
        if energy_delivered == 0:
            energy_delivered = FeederEnergyDaily.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=start_date,
                date__lte=end_date
            ).aggregate(Sum('energy_mwh'))['energy_mwh__sum'] or Decimal('0')
        
        # Get energy billed for these transformers
        energy_billed = MonthlyEnergyBilled.objects.filter(
            transformer_id__in=transformer_ids,
            month=start_date
        ).aggregate(Sum('energy_mwh'))['energy_mwh__sum'] or Decimal('0')
        
        # Calculate efficiency
        efficiency = (energy_billed / energy_delivered * 100) if energy_delivered > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(efficiency, 2)),
            'unit': '%',
            'calculation_method': 'energy_billed / energy_delivered * 100',
            'source': 'MonthlyEnergyBilled, FeederEnergyMonthly',
            'metadata': {
                'energy_delivered_mwh': float(cls.round_decimal(energy_delivered, 2)),
                'energy_billed_mwh': float(cls.round_decimal(energy_billed, 2)),
                'metering_type': metering_type,
                'transformer_count': len(transformer_ids)
            }
        }
    
    @classmethod
    def calculate_collection_efficiency_by_metering_type(cls, period_date, metering_type,
                                                          state=None, district=None):
        """
        Calculate collection efficiency for specific metering type
        KPI: MD Industrial/Non-Industrial/Regions Collection Efficiency (%)
        
        metering_type: 'MD1', 'MD2', 'Non-MD'
        """
        start_date = period_date.replace(day=1)
        
        # Get transformers with customers of this metering type
        transformers_query = Customer.objects.filter(
            metering_type=metering_type
        )
        
        if district:
            transformers_query = transformers_query.filter(transformer__feeder__business_district=district)
        elif state:
            transformers_query = transformers_query.filter(transformer__feeder__business_district__state=state)
        
        transformer_ids = list(transformers_query.values_list('transformer_id', flat=True).distinct())
        
        if not transformer_ids:
            return {
                'value': 0,
                'unit': '%',
                'calculation_method': 'revenue_collected / revenue_billed * 100',
                'source': 'MonthlyCommercialSummary',
                'metadata': {'metering_type': metering_type, 'transformer_count': 0}
            }
        
        # Get revenue billed
        revenue_billed = MonthlyCommercialSummary.objects.filter(
            transformer_id__in=transformer_ids,
            month=start_date
        ).aggregate(Sum('revenue_billed'))['revenue_billed__sum'] or Decimal('0')
        
        # Get revenue collected
        revenue_collected = MonthlyCommercialSummary.objects.filter(
            transformer_id__in=transformer_ids,
            month=start_date
        ).aggregate(Sum('revenue_collected'))['revenue_collected__sum'] or Decimal('0')
        
        # Calculate efficiency
        efficiency = (revenue_collected / revenue_billed * 100) if revenue_billed > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(efficiency, 2)),
            'unit': '%',
            'calculation_method': 'revenue_collected / revenue_billed * 100',
            'source': 'MonthlyCommercialSummary',
            'metadata': {
                'revenue_billed': float(cls.round_decimal(revenue_billed, 2)),
                'revenue_collected': float(cls.round_decimal(revenue_collected, 2)),
                'metering_type': metering_type,
                'transformer_count': len(transformer_ids)
            }
        }
    
    @classmethod
    def calculate_feeders_commercially_ready(cls, period_date, state=None, district=None):
        """
        Calculate number of Band A feeders (commercially ready)
        KPI: Feeders Commercially Ready (count)
        """
        # Get Band A
        try:
            band_a = Band.objects.get(name='A')
        except Band.DoesNotExist:
            return {
                'value': 0,
                'unit': 'feeders',
                'calculation_method': 'count_band_a_feeders',
                'source': 'Feeder',
                'metadata': {'error': 'Band A not found'}
            }
        
        query = Feeder.objects.filter(band=band_a, is_onboarded=True)
        
        if district:
            query = query.filter(business_district=district)
        elif state:
            query = query.filter(business_district__state=state)
        
        count = query.count()
        
        return {
            'value': count,
            'unit': 'feeders',
            'calculation_method': 'count_band_a_onboarded_feeders',
            'source': 'Feeder'
        }
    
    @classmethod
    def calculate_customers_in_billing_system(cls, period_date, state=None, district=None):
        """
        Calculate total active customers
        KPI: Customers in Billing System (count)
        
        Note: All customers who joined on or before the period date are considered active
        since Customer model doesn't track exit dates
        """
        query = Customer.objects.filter(joined_date__lte=period_date)
        
        if district:
            query = query.filter(transformer__feeder__business_district=district)
        elif state:
            query = query.filter(transformer__feeder__business_district__state=state)
        
        count = query.count()
        
        return {
            'value': count,
            'unit': 'customers',
            'calculation_method': 'count_customers_joined_by_period_date',
            'source': 'Customer'
        }
    
    @classmethod
    def calculate_ppm_revenue(cls, period_date, period_type='monthly',
                             state=None, district=None):
        """
        Calculate prepaid meter revenue
        KPI: PPM Revenue Collected (₦M)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        query = DailyCollection.objects.filter(
            date__gte=start_date,
            date__lte=end_date,
            collection_type='Prepaid'
        )
        
        if district:
            query = query.filter(transformer__feeder__business_district=district)
        elif state:
            query = query.filter(transformer__feeder__business_district__state=state)
        
        total_ppm = query.aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        
        # Convert to millions
        total_ppm_millions = total_ppm / 1_000_000
        
        return {
            'value': float(cls.round_decimal(total_ppm_millions, 2)),
            'unit': '₦M',
            'calculation_method': 'sum_prepaid_collections / 1M',
            'source': 'DailyCollection',
            'metadata': {
                'total_naira': float(cls.round_decimal(total_ppm, 2))
            }
        }
    
    @classmethod
    def calculate_customer_attrition_rate(cls, period_date, period_type='monthly',
                                         state=None, district=None):
        """
        Calculate customer attrition rate
        KPI: Customer Attrition Rate (%)
        
        Note: Since Customer model doesn't track exit_date, we calculate based on 
        change in customer count between periods. This is an approximation.
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get previous period
        if period_type == 'monthly':
            prev_start = start_date - relativedelta(months=1)
        elif period_type == 'quarterly':
            prev_start = start_date - relativedelta(months=3)
        else:
            prev_start = start_date - relativedelta(years=1)
        
        # Get customers at start of current period
        current_query = Customer.objects.filter(joined_date__lt=start_date)
        prev_query = Customer.objects.filter(joined_date__lt=prev_start)
        
        if district:
            current_query = current_query.filter(transformer__feeder__business_district=district)
            prev_query = prev_query.filter(transformer__feeder__business_district=district)
        elif state:
            current_query = current_query.filter(transformer__feeder__business_district__state=state)
            prev_query = prev_query.filter(transformer__feeder__business_district__state=state)
        
        current_count = current_query.count()
        prev_count = prev_query.count()
        
        # Calculate net change
        new_customers_query = Customer.objects.filter(
            joined_date__gte=prev_start,
            joined_date__lt=start_date
        )
        if district:
            new_customers_query = new_customers_query.filter(
                transformer__feeder__business_district=district
            )
        elif state:
            new_customers_query = new_customers_query.filter(
                transformer__feeder__business_district__state=state
            )
        
        new_customers = new_customers_query.count()
        
        # Estimated lost customers = prev_count + new_customers - current_count
        estimated_lost = max(0, prev_count + new_customers - current_count)
        
        # Calculate attrition rate (as % of previous period customer base)
        attrition_rate = (Decimal(estimated_lost) / Decimal(prev_count) * 100) if prev_count > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(attrition_rate, 2)),
            'unit': '%',
            'calculation_method': 'estimated_lost_customers / previous_period_customers * 100',
            'source': 'Customer (estimated from customer count changes)',
            'metadata': {
                'previous_period_customers': prev_count,
                'new_customers': new_customers,
                'current_period_customers': current_count,
                'estimated_lost': estimated_lost,
                'note': 'Attrition estimated from customer count changes (Customer model has no exit_date field)'
            }
        }
    
    @classmethod
    def calculate_new_md_customers_value(cls, period_date, period_type='monthly',
                                        state=None, district=None, lookback_months=3):
        """
        Calculate revenue value from new MD customers
        KPI: New MD Customers Value (₦M)
        Consider customers joined within last N months as "new"
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        new_customer_threshold = start_date - relativedelta(months=lookback_months)
        
        # Get new MD customers
        new_md_customers = Customer.objects.filter(
            metering_type__in=['MD1', 'MD2'],
            joined_date__gte=new_customer_threshold,
            joined_date__lte=end_date
        )
        
        if district:
            new_md_customers = new_md_customers.filter(
                transformer__feeder__business_district=district
            )
        elif state:
            new_md_customers = new_md_customers.filter(
                transformer__feeder__business_district__state=state
            )
        
        transformers = list(new_md_customers.values_list('transformer_id', flat=True).distinct())
        
        # Get revenue from these transformers
        revenue = MonthlyCommercialSummary.objects.filter(
            transformer_id__in=transformers,
            month=start_date
        ).aggregate(Sum('revenue_billed'))['revenue_billed__sum'] or Decimal('0')
        
        # Convert to millions
        revenue_millions = revenue / 1_000_000
        
        return {
            'value': float(cls.round_decimal(revenue_millions, 2)),
            'unit': '₦M',
            'calculation_method': f'revenue_from_customers_joined_in_last_{lookback_months}_months / 1M',
            'source': 'MonthlyCommercialSummary, Customer',
            'metadata': {
                'new_md_customers_count': len(transformers),
                'lookback_months': lookback_months,
                'total_naira': float(cls.round_decimal(revenue, 2))
            }
        }


# =============================================================================
# CFO KPI CALCULATORS
# =============================================================================

class CFOKPICalculator(KPICalculationService):
    """Calculator for Chief Financial Officer KPIs"""
    
    @classmethod
    def calculate_cost_to_revenue_ratio(cls, period_date, period_type='monthly',
                                       state=None, district=None):
        """
        Calculate cost-to-revenue ratio
        KPI: Cost-to-Revenue Ratio (%)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get total OPEX
        district_opex = Opex.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        )
        
        if district:
            district_opex = district_opex.filter(district=district)
        elif state:
            district_opex = district_opex.filter(district__state=state)
        
        total_district_opex = district_opex.aggregate(
            Sum('credit')
        )['credit__sum'] or Decimal('0')
        
        # Get HQ OPEX (proportionally allocated)
        hq_opex = HQOpex.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        ).aggregate(Sum('credit'))['credit__sum'] or Decimal('0')
        
        # For simplicity, allocate HQ OPEX proportionally
        if state or district:
            # Get state/district revenue as proportion of total
            if district:
                district_revenue = MonthlyCommercialSummary.objects.filter(
                    transformer__feeder__business_district=district,
                    month=start_date
                ).aggregate(Sum('revenue_collected'))['revenue_collected__sum'] or Decimal('0')
            else:
                district_revenue = MonthlyCommercialSummary.objects.filter(
                    transformer__feeder__business_district__state=state,
                    month=start_date
                ).aggregate(Sum('revenue_collected'))['revenue_collected__sum'] or Decimal('0')
            
            total_revenue = MonthlyCommercialSummary.objects.filter(
                month=start_date
            ).aggregate(Sum('revenue_collected'))['revenue_collected__sum'] or Decimal('0')
            
            proportion = (district_revenue / total_revenue) if total_revenue > 0 else Decimal('0')
            allocated_hq_opex = hq_opex * proportion
        else:
            allocated_hq_opex = hq_opex
        
        total_opex = total_district_opex + allocated_hq_opex
        
        # Get total revenue collected
        revenue_query = MonthlyCommercialSummary.objects.filter(
            month=start_date
        )
        
        if district:
            revenue_query = revenue_query.filter(transformer__feeder__business_district=district)
        elif state:
            revenue_query = revenue_query.filter(transformer__feeder__business_district__state=state)
        
        total_revenue = revenue_query.aggregate(
            Sum('revenue_collected')
        )['revenue_collected__sum'] or Decimal('0')
        
        # Calculate ratio
        ratio = (total_opex / total_revenue * 100) if total_revenue > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(ratio, 2)),
            'unit': '%',
            'calculation_method': 'total_opex / total_revenue * 100',
            'source': 'Opex, HQOpex, MonthlyCommercialSummary',
            'metadata': {
                'total_opex': float(cls.round_decimal(total_opex, 2)),
                'total_revenue': float(cls.round_decimal(total_revenue, 2)),
                'district_opex': float(cls.round_decimal(total_district_opex, 2)),
                'allocated_hq_opex': float(cls.round_decimal(allocated_hq_opex, 2))
            }
        }
    
    @classmethod
    def calculate_opex_per_kwh(cls, period_date, period_type='monthly',
                               state=None, district=None):
        """
        Calculate OPEX per kWh delivered
        KPI: OPEX per kWh Delivered (₦/kWh)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get total OPEX (same logic as cost-to-revenue ratio)
        district_opex = Opex.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        )
        
        if district:
            district_opex = district_opex.filter(district=district)
        elif state:
            district_opex = district_opex.filter(district__state=state)
        
        total_district_opex = district_opex.aggregate(
            Sum('credit')
        )['credit__sum'] or Decimal('0')
        
        hq_opex = HQOpex.objects.filter(
            date__gte=start_date,
            date__lte=end_date
        ).aggregate(Sum('credit'))['credit__sum'] or Decimal('0')
        
        # Allocate HQ OPEX proportionally if filtering by state/district
        if state or district:
            feeders_query = Feeder.objects.filter(is_onboarded=True)
            if district:
                feeders_query = feeders_query.filter(business_district=district)
            elif state:
                feeders_query = feeders_query.filter(business_district__state=state)
            
            feeders = list(feeders_query.values_list('id', flat=True))
            
            district_energy = FeederEnergyMonthly.objects.filter(
                feeder_id__in=feeders,
                period=start_date
            ).aggregate(Sum('energy_mwh'))['energy_mwh__sum'] or Decimal('0')
            
            total_energy = FeederEnergyMonthly.objects.filter(
                period=start_date
            ).aggregate(Sum('energy_mwh'))['energy_mwh__sum'] or Decimal('0')
            
            proportion = (district_energy / total_energy) if total_energy > 0 else Decimal('0')
            allocated_hq_opex = hq_opex * proportion
        else:
            allocated_hq_opex = hq_opex
        
        total_opex = total_district_opex + allocated_hq_opex
        
        # Get energy delivered using SQL (from HourlyLoad)
        location_filter = ""
        params = [start_date, end_date]
        
        if district:
            location_filter = """
                AND feeder_id IN (
                    SELECT id FROM common_feeder 
                    WHERE business_district_id = %s AND is_onboarded = TRUE
                )
            """
            params.append(district.id)
        elif state:
            location_filter = """
                AND feeder_id IN (
                    SELECT f.id FROM common_feeder f
                    INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
                    WHERE bd.state_id = %s AND f.is_onboarded = TRUE
                )
            """
            params.append(state.id)
        else:
            location_filter = """
                AND feeder_id IN (
                    SELECT id FROM common_feeder WHERE is_onboarded = TRUE
                )
            """
        
        query = f"""
            SELECT COALESCE(SUM(load_mw), 0) as total_energy_mwh
            FROM technical_hourlyload
            WHERE date BETWEEN %s AND %s
                AND load_mw > 0
                {location_filter}
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchone()
            energy_mwh = result[0] if result else Decimal('0')
        
        energy_kwh = energy_mwh * 1000  # Convert to kWh
        
        # Calculate OPEX per kWh
        opex_per_kwh = (total_opex / energy_kwh) if energy_kwh > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(opex_per_kwh, 4)),
            'unit': '₦/kWh',
            'calculation_method': 'total_opex / total_energy_kwh',
            'source': 'Opex, HQOpex, HourlyLoad',
            'metadata': {
                'total_opex': float(cls.round_decimal(total_opex, 2)),
                'energy_kwh': float(cls.round_decimal(energy_kwh, 2)),
                'energy_mwh': float(cls.round_decimal(energy_mwh, 2))
            }
        }
    
    @classmethod
    def calculate_collection_to_nbet_ratio(cls, period_date, period_type='monthly'):
        """
        Calculate collection to NBET payment ratio
        KPI: Collection to NBET Payment Ratio
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get total revenue collected
        total_revenue = MonthlyCommercialSummary.objects.filter(
            month=start_date
        ).aggregate(Sum('revenue_collected'))['revenue_collected__sum'] or Decimal('0')
        
        # Get NBET invoice amount
        nbet_invoice = NBETInvoice.objects.filter(
            month=start_date
        ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
        
        # Calculate ratio
        ratio = (total_revenue / nbet_invoice) if nbet_invoice > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(ratio, 2)),
            'unit': 'ratio',
            'calculation_method': 'total_revenue_collected / nbet_invoice_amount',
            'source': 'MonthlyCommercialSummary, NBETInvoice',
            'metadata': {
                'total_revenue': float(cls.round_decimal(total_revenue, 2)),
                'nbet_invoice': float(cls.round_decimal(nbet_invoice, 2)),
                'coverage_percentage': float(cls.round_decimal(ratio * 100, 2))
            }
        }


# =============================================================================
# CHRO KPI CALCULATORS
# =============================================================================

class CHROKPICalculator(KPICalculationService):
    """Calculator for Chief Human Resources Officer KPIs"""
    
    @classmethod
    def calculate_staff_productivity(cls, period_date, period_type='monthly',
                                    state=None, district=None):
        """
        Calculate revenue per staff member
        KPI: Staff Productivity (₦M per staff)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get total revenue
        revenue_query = MonthlyCommercialSummary.objects.filter(
            month=start_date
        )
        
        if district:
            revenue_query = revenue_query.filter(transformer__feeder__business_district=district)
        elif state:
            revenue_query = revenue_query.filter(transformer__feeder__business_district__state=state)
        
        total_revenue = revenue_query.aggregate(
            Sum('revenue_collected')
        )['revenue_collected__sum'] or Decimal('0')
        
        # Get active staff count
        staff_query = Staff.objects.filter(
            hire_date__lte=period_date
        ).filter(
            Q(exit_date__isnull=True) | Q(exit_date__gt=period_date)
        )
        
        if district:
            staff_query = staff_query.filter(district=district)
        elif state:
            staff_query = staff_query.filter(state=state)
        
        staff_count = staff_query.count()
        
        # Calculate productivity
        productivity = (total_revenue / Decimal(staff_count)) if staff_count > 0 else Decimal('0')
        productivity_millions = productivity / 1_000_000
        
        return {
            'value': float(cls.round_decimal(productivity_millions, 2)),
            'unit': '₦M/staff',
            'calculation_method': 'total_revenue / active_staff_count / 1M',
            'source': 'MonthlyCommercialSummary, Staff',
            'metadata': {
                'total_revenue': float(cls.round_decimal(total_revenue, 2)),
                'staff_count': staff_count,
                'revenue_per_staff_naira': float(cls.round_decimal(productivity, 2))
            }
        }
    
    @classmethod
    def calculate_employee_utilization_rate(cls, period_date, state=None, district=None):
        """
        Calculate percentage of staff with assigned roles
        KPI: Employee Utilization Rate (%)
        """
        # Get active staff
        staff_query = Staff.objects.filter(
            hire_date__lte=period_date
        ).filter(
            Q(exit_date__isnull=True) | Q(exit_date__gt=period_date)
        )
        
        if district:
            staff_query = staff_query.filter(district=district)
        elif state:
            staff_query = staff_query.filter(state=state)
        
        total_staff = staff_query.count()
        
        # Get staff with roles assigned
        staff_with_roles = staff_query.filter(role__isnull=False).count()
        
        # Calculate utilization
        utilization = (Decimal(staff_with_roles) / Decimal(total_staff) * 100) if total_staff > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(utilization, 2)),
            'unit': '%',
            'calculation_method': 'staff_with_roles / total_staff * 100',
            'source': 'Staff',
            'metadata': {
                'total_staff': total_staff,
                'staff_with_roles': staff_with_roles
            }
        }
    
    @classmethod
    def calculate_wage_bill_vs_revenue(cls, period_date, period_type='monthly',
                                      state=None, district=None):
        """
        Calculate wage bill as percentage of revenue
        KPI: Wage Bill vs Revenue (%)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get total salaries
        salary_query = SalaryPayment.objects.filter(
            month=start_date
        )
        
        if district:
            salary_query = salary_query.filter(district=district)
        elif state:
            salary_query = salary_query.filter(district__state=state)
        
        total_salaries = salary_query.aggregate(
            Sum('amount')
        )['amount__sum'] or Decimal('0')
        
        # Get total revenue
        revenue_query = MonthlyCommercialSummary.objects.filter(
            month=start_date
        )
        
        if district:
            revenue_query = revenue_query.filter(transformer__feeder__business_district=district)
        elif state:
            revenue_query = revenue_query.filter(transformer__feeder__business_district__state=state)
        
        total_revenue = revenue_query.aggregate(
            Sum('revenue_collected')
        )['revenue_collected__sum'] or Decimal('0')
        
        # Calculate ratio
        ratio = (total_salaries / total_revenue * 100) if total_revenue > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(ratio, 2)),
            'unit': '%',
            'calculation_method': 'total_salaries / total_revenue * 100',
            'source': 'SalaryPayment, MonthlyCommercialSummary',
            'metadata': {
                'total_salaries': float(cls.round_decimal(total_salaries, 2)),
                'total_revenue': float(cls.round_decimal(total_revenue, 2))
            }
        }
    
    @classmethod
    def calculate_wage_bill_reduction(cls, period_date, period_type='monthly',
                                     baseline_year=2024, state=None, district=None):
        """
        Calculate wage bill reduction vs baseline year
        KPI: Wage Bill Reduction vs 2024 Baseline (%)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get current period wage bill
        current_salary_query = SalaryPayment.objects.filter(
            month=start_date
        )
        
        if district:
            current_salary_query = current_salary_query.filter(district=district)
        elif state:
            current_salary_query = current_salary_query.filter(district__state=state)
        
        current_wage_bill = current_salary_query.aggregate(
            Sum('amount')
        )['amount__sum'] or Decimal('0')
        
        # Get baseline year same period wage bill
        baseline_date = start_date.replace(year=baseline_year)
        
        baseline_salary_query = SalaryPayment.objects.filter(
            month=baseline_date
        )
        
        if district:
            baseline_salary_query = baseline_salary_query.filter(district=district)
        elif state:
            baseline_salary_query = baseline_salary_query.filter(district__state=state)
        
        baseline_wage_bill = baseline_salary_query.aggregate(
            Sum('amount')
        )['amount__sum'] or Decimal('0')
        
        # Calculate reduction
        if baseline_wage_bill > 0:
            reduction = ((baseline_wage_bill - current_wage_bill) / baseline_wage_bill * 100)
        else:
            reduction = Decimal('0')
        
        return {
            'value': float(cls.round_decimal(reduction, 2)),
            'unit': '%',
            'calculation_method': f'(baseline_{baseline_year} - current) / baseline * 100',
            'source': 'SalaryPayment',
            'metadata': {
                'current_wage_bill': float(cls.round_decimal(current_wage_bill, 2)),
                'baseline_wage_bill': float(cls.round_decimal(baseline_wage_bill, 2)),
                'baseline_year': baseline_year,
                'absolute_reduction': float(cls.round_decimal(baseline_wage_bill - current_wage_bill, 2))
            }
        }
    
    @classmethod
    def calculate_staff_attrition_rate(cls, period_date, period_type='monthly',
                                      state=None, district=None):
        """
        Calculate staff attrition rate
        KPI: Staff Attrition Rate (%)
        """
        start_date, end_date = cls.get_period_range(period_date, period_type)
        
        # Get staff at start of period
        staff_at_start = Staff.objects.filter(
            hire_date__lt=start_date
        ).filter(
            Q(exit_date__isnull=True) | Q(exit_date__gte=start_date)
        )
        
        if district:
            staff_at_start = staff_at_start.filter(district=district)
        elif state:
            staff_at_start = staff_at_start.filter(state=state)
        
        start_count = staff_at_start.count()
        
        # Get staff who left during period
        staff_left = Staff.objects.filter(
            exit_date__gte=start_date,
            exit_date__lte=end_date
        )
        
        if district:
            staff_left = staff_left.filter(district=district)
        elif state:
            staff_left = staff_left.filter(state=state)
        
        left_count = staff_left.count()
        
        # Calculate attrition
        attrition = (Decimal(left_count) / Decimal(start_count) * 100) if start_count > 0 else Decimal('0')
        
        return {
            'value': float(cls.round_decimal(attrition, 2)),
            'unit': '%',
            'calculation_method': 'staff_left / staff_at_start * 100',
            'source': 'Staff',
            'metadata': {
                'staff_at_start': start_count,
                'staff_left': left_count
            }
        }


# =============================================================================
# UNIFIED KPI CALCULATOR
# =============================================================================

class UnifiedKPICalculator:
    """
    Unified interface for calculating any KPI
    Maps KPI names to their calculation methods
    """
    
    # KPI mapping dictionary
    KPI_CALCULATORS = {
        # CTO KPIs
        'energy_delivered_gwh': CTOKPICalculator.calculate_energy_delivered,
        'avg_hours_of_supply': CTOKPICalculator.calculate_avg_hours_of_supply,
        'grid_offtake_capacity': CTOKPICalculator.calculate_grid_offtake_capacity,
        'sla_compliance': CTOKPICalculator.calculate_sla_compliance,
        'system_availability': CTOKPICalculator.calculate_system_availability,
        'avg_interruption_duration': CTOKPICalculator.calculate_avg_interruption_duration,
        'saifi': CTOKPICalculator.calculate_saifi,
        'saidi': CTOKPICalculator.calculate_saidi,
        
        # CCO KPIs
        'billing_efficiency_md1': lambda *args, **kwargs: CCOKPICalculator.calculate_billing_efficiency_by_metering_type(*args, metering_type='MD1', **kwargs),
        'billing_efficiency_md2': lambda *args, **kwargs: CCOKPICalculator.calculate_billing_efficiency_by_metering_type(*args, metering_type='MD2', **kwargs),
        'billing_efficiency_non_md': lambda *args, **kwargs: CCOKPICalculator.calculate_billing_efficiency_by_metering_type(*args, metering_type='Non-MD', **kwargs),
        'collection_efficiency_md1': lambda *args, **kwargs: CCOKPICalculator.calculate_collection_efficiency_by_metering_type(*args, metering_type='MD1', **kwargs),
        'collection_efficiency_md2': lambda *args, **kwargs: CCOKPICalculator.calculate_collection_efficiency_by_metering_type(*args, metering_type='MD2', **kwargs),
        'collection_efficiency_non_md': lambda *args, **kwargs: CCOKPICalculator.calculate_collection_efficiency_by_metering_type(*args, metering_type='Non-MD', **kwargs),
        'feeders_commercially_ready': CCOKPICalculator.calculate_feeders_commercially_ready,
        'customers_in_billing_system': CCOKPICalculator.calculate_customers_in_billing_system,
        'ppm_revenue': CCOKPICalculator.calculate_ppm_revenue,
        'customer_attrition_rate': CCOKPICalculator.calculate_customer_attrition_rate,
        'new_md_customers_value': CCOKPICalculator.calculate_new_md_customers_value,
        
        # CFO KPIs
        'cost_to_revenue_ratio': CFOKPICalculator.calculate_cost_to_revenue_ratio,
        'opex_per_kwh': CFOKPICalculator.calculate_opex_per_kwh,
        'collection_to_nbet_ratio': CFOKPICalculator.calculate_collection_to_nbet_ratio,
        
        # CHRO KPIs
        'staff_productivity': CHROKPICalculator.calculate_staff_productivity,
        'employee_utilization_rate': CHROKPICalculator.calculate_employee_utilization_rate,
        'wage_bill_vs_revenue': CHROKPICalculator.calculate_wage_bill_vs_revenue,
        'wage_bill_reduction': CHROKPICalculator.calculate_wage_bill_reduction,
        'staff_attrition_rate': CHROKPICalculator.calculate_staff_attrition_rate,
    }
    
    @classmethod
    def calculate_kpi(cls, kpi_key, period_date, period_type='monthly', **kwargs):
        """
        Calculate any KPI by its key
        
        Args:
            kpi_key: KPI identifier (e.g., 'energy_delivered_gwh')
            period_date: Date for calculation
            period_type: 'monthly', 'quarterly', 'annually'
            **kwargs: Additional parameters (state, district, feeder, etc.)
        
        Returns:
            dict: Calculation result with value, unit, method, source
        """
        calculator = cls.KPI_CALCULATORS.get(kpi_key)
        
        if not calculator:
            return {
                'value': None,
                'unit': None,
                'calculation_method': 'not_found',
                'source': None,
                'error': f'No calculator found for KPI: {kpi_key}'
            }
        
        try:
            result = calculator(period_date, period_type=period_type, **kwargs)
            result['kpi_key'] = kpi_key
            result['period_date'] = period_date.isoformat()
            result['period_type'] = period_type
            if 'calculated_at' not in result:
                result['calculated_at'] = timezone.now().isoformat()
            return result
        except Exception as e:
            import traceback
            return {
                'value': None,
                'unit': None,
                'calculation_method': 'error',
                'source': None,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'kpi_key': kpi_key
            }
    
    @classmethod
    def calculate_multiple_kpis(cls, kpi_keys, period_date, period_type='monthly', **kwargs):
        """
        Calculate multiple KPIs at once
        
        Args:
            kpi_keys: List of KPI identifiers
            period_date: Date for calculation
            period_type: 'monthly', 'quarterly', 'annually'
            **kwargs: Additional parameters
        
        Returns:
            dict: Results keyed by KPI identifier
        """
        results = {}
        for kpi_key in kpi_keys:
            results[kpi_key] = cls.calculate_kpi(kpi_key, period_date, period_type, **kwargs)
        
        return results
    
    @classmethod
    def get_available_kpis(cls):
        """Get list of all available KPI keys"""
        return list(cls.KPI_CALCULATORS.keys())
    
    @classmethod
    def get_kpis_by_role(cls, executive_role):
        """Get KPI keys for a specific executive role"""
        role_kpis = {
            'CTO': [
                'energy_delivered_gwh',
                'avg_hours_of_supply',
                'grid_offtake_capacity',
                'sla_compliance',
                'system_availability',
                'avg_interruption_duration',
                'saifi',
                'saidi'
            ],
            'CCO': [
                'billing_efficiency_md1',
                'billing_efficiency_md2',
                'billing_efficiency_non_md',
                'collection_efficiency_md1',
                'collection_efficiency_md2',
                'collection_efficiency_non_md',
                'feeders_commercially_ready',
                'customers_in_billing_system',
                'ppm_revenue',
                'customer_attrition_rate',
                'new_md_customers_value'
            ],
            'CFO': [
                'cost_to_revenue_ratio',
                'opex_per_kwh',
                'collection_to_nbet_ratio'
            ],
            'CHRO': [
                'staff_productivity',
                'employee_utilization_rate',
                'wage_bill_vs_revenue',
                'wage_bill_reduction',
                'staff_attrition_rate'
            ]
        }
        
        return role_kpis.get(executive_role.upper(), [])