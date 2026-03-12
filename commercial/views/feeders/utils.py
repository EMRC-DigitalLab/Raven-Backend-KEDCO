from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, FeederEnergyDaily, FeederEnergyMonthly


def safe_decimal(value):
    """Convert value to Decimal safely"""
    try:
        return Decimal(str(value or 0))
    except (TypeError, ValueError):
        return Decimal(0)


def get_energy_delivered_for_feeder(feeder, start_date, end_date):
    """Get energy delivered using the most efficient method available"""
    try:
        # For single month periods, try monthly aggregates first
        if (end_date - start_date).days <= 32:  # Single month
            period_start = start_date
            delivered_mwh = FeederEnergyMonthly.objects.filter(
                feeder=feeder,
                period=period_start,
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
            
            if delivered_mwh:
                return safe_decimal(delivered_mwh)
        
        # Try daily aggregation
        period_end = end_date - timedelta(days=1)
        delivered_mwh = FeederEnergyDaily.objects.filter(
            feeder=feeder,
            date__gte=start_date,
            date__lte=period_end,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered_mwh:
            return safe_decimal(delivered_mwh)
            
        # Final fallback to EnergyDelivered
        delivered_mwh = EnergyDelivered.objects.filter(
            feeder=feeder,
            date__gte=start_date,
            date__lt=end_date
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return safe_decimal(delivered_mwh)
        
    except Exception:
        return Decimal(0)


def calculate_atcc_metrics(feeder, start_date, end_date):
    """Calculate AT&C metrics for a feeder with optimized queries and accurate calculations"""
    
    # Energy Delivered - use optimized method
    delivered = get_energy_delivered_for_feeder(feeder, start_date, end_date)

    # Energy Billed
    billed = MonthlyEnergyBilled.objects.filter(
        feeder=feeder,
        month__gte=start_date,
        month__lt=end_date
    ).aggregate(energy_mwh_sum=Sum("energy_mwh"))['energy_mwh_sum']
    billed = safe_decimal(billed)

    # Commercial data - use direct transformer access instead of sales rep
    summaries = MonthlyCommercialSummary.objects.filter(
        transformer__feeder=feeder,
        month__gte=start_date,
        month__lt=end_date
    ).aggregate(
        revenue_collected_sum=Sum("revenue_collected"), 
        revenue_billed_sum=Sum("revenue_billed")
    )

    revenue_collected = safe_decimal(summaries["revenue_collected_sum"])
    revenue_billed = safe_decimal(summaries["revenue_billed_sum"])

    # Calculate metrics with proper error handling and bounds checking
    try:
        # Billing efficiency = (Energy Billed / Energy Delivered) * 100
        billing_eff = (billed / delivered * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if delivered > 0 else Decimal(0)
        
        # Cap billing efficiency at 100% (energy billed cannot exceed energy delivered)
        billing_eff = min(billing_eff, Decimal(100))
        
        # Collection efficiency = (Revenue Collected / Revenue Billed) * 100
        collection_eff = (revenue_collected / revenue_billed * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if revenue_billed > 0 else Decimal(0)
        
        # Cap collection efficiency at 100% (collections cannot exceed billings)
        collection_eff = min(collection_eff, Decimal(100))
        
        # AT&C losses = 100% - (Billing Efficiency * Collection Efficiency / 100)
        atcc = (Decimal(100) - (billing_eff * collection_eff / 100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        
        # Ensure AT&C is never negative (minimum 0%)
        atcc = max(atcc, Decimal(0))
        
        # Energy collected = Energy Delivered * (Collection Efficiency / 100)
        collected_energy = (delivered * collection_eff / 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        
    except Exception:
        billing_eff = collection_eff = atcc = collected_energy = Decimal(0)

    return {
        "id": str(feeder.id),
        "name": feeder.name,
        "state": feeder.business_district.state.name if feeder.business_district else None,
        "business_district": feeder.business_district.name if feeder.business_district else None,
        "substation": feeder.substation.name if feeder.substation else None,
        "energy_delivered": float(delivered),
        "energy_billed": float(billed),
        "energy_collected": float(collected_energy),
        "billing_efficiency": float(billing_eff),
        "collection_efficiency": float(collection_eff),
        "atcc": float(atcc),
        "voltage_level": feeder.voltage_level,
    }