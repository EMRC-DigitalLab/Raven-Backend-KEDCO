# hr/management/commands/seed_executive_kpis.py - COMPLETE VERSION
# Create this directory structure: hr/management/commands/
# Run with: python manage.py seed_executive_kpis

from django.core.management.base import BaseCommand
from decimal import Decimal
from hr.models import ExecutiveKPIDefinition


class Command(BaseCommand):
    help = 'Seed database with KEDCO executive KPI definitions'

    def handle(self, *args, **options):
        self.stdout.write('Seeding Executive KPI definitions...')
        
        # CFO KPIs
        cfo_kpis = [
            {
                'executive_role': 'CFO',
                'category': 'financial',
                'name': 'Cost-to-revenue ratio optimization',
                'description': 'Reduce operational cost-to-revenue ratio from current 12-14% to target 5-8%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_min': Decimal('5.0'),
                'target_max': Decimal('8.0'),
                'is_range_target': True,
                'is_reverse_polarity': True,  # Lower is better
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CFO',
                'category': 'financial',
                'name': 'Admin & general expenses within budget',
                'description': 'Ensure admin and general expenses stay under 100% of allocated budget',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('100.0'),
                'is_range_target': False,
                'is_reverse_polarity': True,  # Lower is better
                'deadline': 'July 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CFO',
                'category': 'financial',
                'name': 'Monthly Internally Generated Revenue',
                'description': 'Increase monthly IGR from current ₦38M to target ₦150M',
                'data_type': 'currency',
                'unit': '₦',
                'priority': 'critical',
                'target_value': Decimal('150000000.0'),  # ₦150M
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
        ]

        # CTO KPIs - Continuing from where it stopped
        cto_kpis = [
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Feeders technically ready for Band A upgrade',
                'description': 'Upgrade feeders to be technically ready for Band A service (currently 0, target 17)',
                'data_type': 'integer',
                'unit': ' feeders',
                'priority': 'high',
                'target_value': Decimal('17.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Grid energy offtake (150-170 GWh)',
                'description': 'Increase grid energy offtake from current 130 GWh to target 150-170 GWh',
                'data_type': 'decimal',
                'unit': 'GWh',
                'priority': 'high',
                'target_min': Decimal('150.0'),
                'target_max': Decimal('170.0'),
                'is_range_target': True,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Energy delivered to Band A feeders',
                'description': 'Improve energy delivery to Band A feeders from current 40% to target 60%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('60.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Band A feeders SLA compliance',
                'description': 'Maintain 100% SLA compliance for Band A feeders',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('100.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'July 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Monthly Internally Generated Revenue',
                'description': 'Generate ₦45M monthly IGR from technical improvements',
                'data_type': 'currency',
                'unit': '₦',
                'priority': 'medium',
                'target_value': Decimal('45000000.0'),  # ₦45M
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
        ]

        # CCO KPIs
        cco_kpis = [
            # Billing Efficiency KPIs
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Industrial Billing Efficiency',
                'description': 'Improve MD Industrial billing efficiency from 84% to 95%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('95.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Non-Industrial Billing Efficiency',
                'description': 'Improve MD Non-Industrial billing efficiency from 68% to 85%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('85.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Regions Billing Efficiency',
                'description': 'Improve regions billing efficiency from 78% to 85%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('85.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Smart meters streaming on AMI',
                'description': 'Connect 100% of smart meters to AMI (currently 0.01%)',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('100.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Meters acquired and installed',
                'description': 'Acquire and install 83,000 new meters',
                'data_type': 'integer',
                'unit': ' meters',
                'priority': 'high',
                'target_value': Decimal('83000.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            # Collection Efficiency KPIs
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Industrial Collection Efficiency',
                'description': 'Improve MD Industrial collection efficiency from 78% to 90%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('90.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'July 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Non-Industrial Collection Efficiency',
                'description': 'Improve MD Non-Industrial collection efficiency from 73% to 80%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('80.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'July 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Regions Collection Efficiency',
                'description': 'Improve regions collection efficiency from 45% to 60%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('60.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'July 2025',
                'measurement_frequency': 'monthly',
            },
            # Band A Growth & Customer Expansion KPIs
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Feeders commercially ready for Band A',
                'description': 'Prepare 17 feeders commercially for Band A upgrade',
                'data_type': 'integer',
                'unit': ' feeders',
                'priority': 'critical',
                'target_value': Decimal('17.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Customers in billing system',
                'description': 'Onboard 1 million customers into billing system',
                'data_type': 'integer',
                'unit': ' customers',
                'priority': 'high',
                'target_value': Decimal('1000000.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'PPM collected revenue',
                'description': 'Increase PPM revenue from ₦500M to ₦1B',
                'data_type': 'currency',
                'unit': '₦',
                'priority': 'high',
                'target_value': Decimal('1000000000.0'),  # ₦1B
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Mamuda monthly energy offtake',
                'description': 'Increase Mamuda factory offtake from 2.8 GWh to 3.0 GWh',
                'data_type': 'decimal',
                'unit': ' GWh',
                'priority': 'high',
                'target_value': Decimal('3.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Top 20 MD customer attrition rate',
                'description': 'Maintain 0% attrition of top 20 MD customers',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('0.0'),
                'is_range_target': False,
                'is_reverse_polarity': True,  # Lower is better
                'deadline': 'Ongoing',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Value of new MD customers onboarded',
                'description': 'Onboard new MD customers worth ₦1B in potential revenue',
                'data_type': 'currency',
                'unit': '₦',
                'priority': 'high',
                'target_value': Decimal('1000000000.0'),  # ₦1B
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Monthly Internally Generated Revenue',
                'description': 'Generate ₦105M monthly IGR from commercial activities',
                'data_type': 'currency',
                'unit': '₦',
                'priority': 'critical',
                'target_value': Decimal('105000000.0'),  # ₦105M
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
        ]

        # CHRO KPIs
        chro_kpis = [
            {
                'executive_role': 'CHRO',
                'category': 'hr',
                'name': 'Monthly staff productivity per employee',
                'description': 'Increase staff productivity from ₦3.1M to ₦5.5M per employee monthly',
                'data_type': 'currency',
                'unit': 'M',
                'priority': 'critical',
                'target_value': Decimal('5.5'),  # ₦5.5M
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CHRO',
                'category': 'hr',
                'name': 'C-Suite executive appraisals completed',
                'description': 'Complete 6 executive appraisals (5 monthly + 1 yearly)',
                'data_type': 'integer',
                'unit': ' appraisals',
                'priority': 'high',
                'target_value': Decimal('6.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Year-end 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CHRO',
                'category': 'hr',
                'name': 'Wage bill reduction achievement',
                'description': 'Achieve 15% reduction in overall wage bill',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('15.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'quarterly',
            },
        ]

        # Combine all KPIs
        all_kpis = cfo_kpis + cto_kpis + cco_kpis + chro_kpis

        # Create KPI definitions
        created_count = 0
        updated_count = 0
        
        for kpi_data in all_kpis:
            kpi, created = ExecutiveKPIDefinition.objects.get_or_create(
                executive_role=kpi_data['executive_role'],
                name=kpi_data['name'],
                defaults=kpi_data
            )
            
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Created KPI: {kpi_data['executive_role']} - {kpi_data['name']}"
                    )
                )
            else:
                # Update existing KPI if needed
                updated = False
                for field, value in kpi_data.items():
                    if hasattr(kpi, field) and getattr(kpi, field) != value:
                        setattr(kpi, field, value)
                        updated = True
                
                if updated:
                    kpi.save()
                    updated_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"↻ Updated KPI: {kpi_data['executive_role']} - {kpi_data['name']}"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.HTTP_INFO(
                            f"- KPI exists: {kpi_data['executive_role']} - {kpi_data['name']}"
                        )
                    )

        # Summary
        self.stdout.write("\n" + "="*60)
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ SEEDING COMPLETED SUCCESSFULLY!"
            )
        )
        self.stdout.write(f"   • Created: {created_count} new KPIs")
        self.stdout.write(f"   • Updated: {updated_count} existing KPIs")
        self.stdout.write(f"   • Total processed: {len(all_kpis)} KPIs")

        # Display summary by role
        self.stdout.write("\n📊 KPI BREAKDOWN BY EXECUTIVE ROLE:")
        for role_code, role_name in [
            ('CFO', 'Chief Financial Officer'),
            ('CTO', 'Chief Technology Officer'),
            ('CCO', 'Chief Commercial Officer'),
            ('CHRO', 'Chief Human Resources Officer')
        ]:
            count = ExecutiveKPIDefinition.objects.filter(
                executive_role=role_code, 
                is_active=True
            ).count()
            self.stdout.write(f"   • {role_code} ({role_name}): {count} KPIs")

        # Display category breakdown
        self.stdout.write("\n📋 KPI BREAKDOWN BY CATEGORY:")
        categories = ExecutiveKPIDefinition.objects.filter(is_active=True).values_list(
            'category', flat=True
        ).distinct()
        
        # Category display mapping
        category_mapping = {
            'financial': 'Financial Excellence',
            'technical': 'Technical Operations', 
            'commercial': 'Commercial Performance',
            'hr': 'Human Resources'
        }
        
        for category in categories:
            count = ExecutiveKPIDefinition.objects.filter(
                category=category, 
                is_active=True
            ).count()
            category_display = category_mapping.get(category, category.title())
            self.stdout.write(f"   • {category_display}: {count} KPIs")

        self.stdout.write("="*60)
        self.stdout.write(
            self.style.SUCCESS(
                "🚀 Ready to proceed with API endpoint creation!"
            )
        )