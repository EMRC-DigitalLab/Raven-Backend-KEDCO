# hr/management/commands/sync_board_kpis.py
"""
Management command to sync Board KPIs with official targets from presentation
This will add missing KPIs, update targets, and mark obsolete ones as inactive
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from hr.models import ExecutiveKPIDefinition
from decimal import Decimal


class Command(BaseCommand):
    help = 'Sync Board KPIs with official targets from presentation images'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting Board KPI sync...'))
        
        with transaction.atomic():
            # First, mark all existing KPIs as inactive (we'll reactivate the ones we keep)
            ExecutiveKPIDefinition.objects.all().update(is_active=False)
            
            # CFO KPIs
            self.sync_cfo_kpis()
            
            # CTO KPIs
            self.sync_cto_kpis()
            
            # CCO KPIs
            self.sync_cco_kpis()
            
            # CHRO KPIs
            self.sync_chro_kpis()
        
        self.stdout.write(self.style.SUCCESS('Board KPI sync completed successfully!'))
    
    def sync_cfo_kpis(self):
        """Sync CFO KPIs based on Image 1"""
        self.stdout.write('Syncing CFO KPIs...')
        
        cfo_kpis = [
            {
                'executive_role': 'CFO',
                'category': 'financial',
                'name': 'Cost-to-Revenue Ratio',
                'description': 'Ratio of operational costs to revenue collected',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_min': Decimal('5.0'),
                'target_max': Decimal('8.0'),
                'is_range_target': True,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CFO',
                'category': 'financial',
                'name': 'Administration & General Expenses Budget Adherence',
                'description': '<100% of administration and general expenses budget spent',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('100.0'),
                'is_range_target': False,
                'is_reverse_polarity': True,  # Lower is better
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CFO',
                'category': 'financial',
                'name': 'Monthly Internally Generated Revenue',
                'description': 'Monthly Internally Generated Revenue target',
                'data_type': 'currency',
                'unit': '₦M',
                'priority': 'high',
                'target_value': Decimal('150.0'),  # N150Mn
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
        ]
        
        for kpi_data in cfo_kpis:
            kpi, created = ExecutiveKPIDefinition.objects.update_or_create(
                executive_role=kpi_data['executive_role'],
                name=kpi_data['name'],
                defaults=kpi_data
            )
            kpi.is_active = True
            kpi.save()
            
            action = 'Created' if created else 'Updated'
            self.stdout.write(f'  {action}: {kpi.name}')
    
    def sync_cto_kpis(self):
        """Sync CTO KPIs based on Image 2"""
        self.stdout.write('Syncing CTO KPIs...')
        
        cto_kpis = [
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Feeders Technically Ready for Band A Upgrade',
                'description': '17 feeders technically ready for upgrade to band A',
                'data_type': 'integer',
                'unit': ' feeders',
                'priority': 'high',
                'target_value': Decimal('17'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Grid Energy Offtake',
                'description': 'Grid energy offtake capacity from transmission',
                'data_type': 'decimal',
                'unit': 'GWh',
                'priority': 'critical',
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
                'name': 'Energy Delivered to Band A Feeders',
                'description': '>=60% of energy delivered to Band A feeders',
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
                'name': 'Band A Feeders SLA Compliance',
                'description': '100% of Band A feeders compliant with SLA',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('100.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CTO',
                'category': 'technical',
                'name': 'Monthly Internally Generated Revenue',
                'description': 'Monthly Internally Generated Revenue target',
                'data_type': 'currency',
                'unit': '₦M',
                'priority': 'high',
                'target_value': Decimal('45.0'),  # N45Mn
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
        ]
        
        for kpi_data in cto_kpis:
            kpi, created = ExecutiveKPIDefinition.objects.update_or_create(
                executive_role=kpi_data['executive_role'],
                name=kpi_data['name'],
                defaults=kpi_data
            )
            kpi.is_active = True
            kpi.save()
            
            action = 'Created' if created else 'Updated'
            self.stdout.write(f'  {action}: {kpi.name}')
    
    def sync_cco_kpis(self):
        """Sync CCO KPIs based on Image 3"""
        self.stdout.write('Syncing CCO KPIs...')
        
        cco_kpis = [
            # Billing Efficiency
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Industrial Billing Efficiency',
                'description': 'MD Ind BE % at 95%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('95.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Non-Industrial Billing Efficiency',
                'description': 'MD Non Ind. BE % at 85%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('85.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Regions Billing Efficiency',
                'description': 'Regions BE % at 85%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('85.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Smart Meters Streaming on AMI',
                'description': '100% of smart meters streaming on AMI',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('100.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Meters Acquired and Installed',
                'description': '83k meters acquired and installed',
                'data_type': 'integer',
                'unit': ' meters',
                'priority': 'high',
                'target_value': Decimal('83000'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            
            # Collection Efficiency
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Industrial Collection Efficiency',
                'description': 'MD Ind. CE % at 90%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('90.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'MD Non-Industrial Collection Efficiency',
                'description': 'MD Non Ind. CE % at 80%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'critical',
                'target_value': Decimal('80.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Regions Collection Efficiency',
                'description': 'Regions CE % at 60%',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('60.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            
            # Band A Growth
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Feeders Commercially Ready for Band A',
                'description': '17 feeders commercially ready for upgrade to band A',
                'data_type': 'integer',
                'unit': ' feeders',
                'priority': 'high',
                'target_value': Decimal('17'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Customers Integrated into Billing System',
                'description': '1 million customers integrated into billing system',
                'data_type': 'integer',
                'unit': ' customers',
                'priority': 'high',
                'target_value': Decimal('1000000'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'PPM Collected Revenue',
                'description': 'PPM collected revenue',
                'data_type': 'currency',
                'unit': '₦M',
                'priority': 'high',
                'target_value': Decimal('500.0'),  # TBD in image, using placeholder
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Mamuda Monthly Energy Offtake',
                'description': 'Mamuda monthly energy offtake maintained at 3GWh',
                'data_type': 'decimal',
                'unit': 'GWh',
                'priority': 'medium',
                'target_value': Decimal('3.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Top 20 MD Customer Churn Rate',
                'description': '0% churn rate of top 20 MD customers',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('0.0'),
                'is_range_target': False,
                'is_reverse_polarity': True,  # Lower is better
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'New MD Industrial Customer Value',
                'description': 'Value of new MD Ind. customers onboarded to the grid at N1Bn',
                'data_type': 'currency',
                'unit': '₦B',
                'priority': 'high',
                'target_value': Decimal('1.0'),  # N1Bn
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            
            # Revenue
            {
                'executive_role': 'CCO',
                'category': 'commercial',
                'name': 'Monthly Internally Generated Revenue',
                'description': 'Monthly Internally Generated Revenue target',
                'data_type': 'currency',
                'unit': '₦M',
                'priority': 'high',
                'target_value': Decimal('105.0'),  # N105Mn
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
        ]
        
        for kpi_data in cco_kpis:
            kpi, created = ExecutiveKPIDefinition.objects.update_or_create(
                executive_role=kpi_data['executive_role'],
                name=kpi_data['name'],
                defaults=kpi_data
            )
            kpi.is_active = True
            kpi.save()
            
            action = 'Created' if created else 'Updated'
            self.stdout.write(f'  {action}: {kpi.name}')
    
    def sync_chro_kpis(self):
        """Sync CHRO KPIs based on Image 4"""
        self.stdout.write('Syncing CHRO KPIs...')
        
        chro_kpis = [
            {
                'executive_role': 'CHRO',
                'category': 'hr',
                'name': 'Monthly Staff Productivity',
                'description': 'Monthly staff productivity at > N5.5Mn per employee',
                'data_type': 'currency',
                'unit': '₦M',
                'priority': 'high',
                'target_value': Decimal('5.5'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Monthly',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CHRO',
                'category': 'hr',
                'name': 'C-Suite Executive Appraisals',
                'description': '5 monthly and 1 year-end appraisal conducted for C-suite executives (by year-end)',
                'data_type': 'integer',
                'unit': ' appraisals',
                'priority': 'medium',
                'target_value': Decimal('6'),  # 5 monthly + 1 year-end
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
            {
                'executive_role': 'CHRO',
                'category': 'hr',
                'name': 'Wage Bill Reduction',
                'description': '15% reduction in wage bill',
                'data_type': 'percentage',
                'unit': '%',
                'priority': 'high',
                'target_value': Decimal('15.0'),
                'is_range_target': False,
                'is_reverse_polarity': False,
                'deadline': 'Q4 2025',
                'measurement_frequency': 'monthly',
            },
        ]
        
        for kpi_data in chro_kpis:
            kpi, created = ExecutiveKPIDefinition.objects.update_or_create(
                executive_role=kpi_data['executive_role'],
                name=kpi_data['name'],
                defaults=kpi_data
            )
            kpi.is_active = True
            kpi.save()
            
            action = 'Created' if created else 'Updated'
            self.stdout.write(f'  {action}: {kpi.name}')
        
        self.stdout.write(self.style.SUCCESS(f'Synced {len(chro_kpis)} CHRO KPIs'))