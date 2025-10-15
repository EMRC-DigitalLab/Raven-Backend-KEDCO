# hr/management/commands/create_sample_performance.py
from django.core.management.base import BaseCommand
from decimal import Decimal
from datetime import date, timedelta
from hr.models import ExecutiveKPIDefinition, ExecutivePerformance, ExecutiveRole
from users.models import User
import random


class Command(BaseCommand):
    help = 'Create sample performance data for testing the KPI APIs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--months',
            type=int,
            default=6,
            help='Number of months of sample data to create'
        )

    def handle(self, *args, **options):
        months = options['months']
        self.stdout.write(f'Creating {months} months of sample performance data...')
        
        # Get a user to assign as creator (or create a default one)
        try:
            creator_user = User.objects.filter(is_superuser=True).first()
            if not creator_user:
                creator_user = User.objects.first()
        except:
            creator_user = None
        
        # Sample performance data for each executive role
        sample_data = {
            'CFO': {
                'Cost-to-revenue ratio optimization': {
                    'base_value': 13.0,
                    'target_value': 6.5,  # Middle of 5-8% range
                    'trend': 'improving',  # Decreasing is good
                    'variance': 1.0
                },
                'Admin & general expenses within budget': {
                    'base_value': 95.0,
                    'target_value': 95.0,  # Under 100%
                    'trend': 'stable',
                    'variance': 5.0
                },
                'Monthly Internally Generated Revenue': {
                    'base_value': 38000000.0,
                    'target_value': 150000000.0,
                    'trend': 'improving',
                    'variance': 5000000.0
                }
            },
            'CTO': {
                'Feeders technically ready for Band A upgrade': {
                    'base_value': 0.0,
                    'target_value': 17.0,
                    'trend': 'improving',
                    'variance': 1.0
                },
                'Grid energy offtake (150-170 GWh)': {
                    'base_value': 130.0,
                    'target_value': 160.0,  # Middle of range
                    'trend': 'improving',
                    'variance': 10.0
                },
                'Energy delivered to Band A feeders': {
                    'base_value': 40.0,
                    'target_value': 60.0,
                    'trend': 'improving',
                    'variance': 5.0
                },
                'Band A feeders SLA compliance': {
                    'base_value': 95.0,
                    'target_value': 100.0,
                    'trend': 'improving',
                    'variance': 3.0
                },
                'Monthly Internally Generated Revenue': {
                    'base_value': 0.0,
                    'target_value': 45000000.0,
                    'trend': 'improving',
                    'variance': 2000000.0
                }
            },
            'CCO': {
                'MD Industrial Billing Efficiency': {
                    'base_value': 84.0,
                    'target_value': 95.0,
                    'trend': 'improving',
                    'variance': 3.0
                },
                'MD Non-Industrial Billing Efficiency': {
                    'base_value': 68.0,
                    'target_value': 85.0,
                    'trend': 'improving',
                    'variance': 4.0
                },
                'Regions Billing Efficiency': {
                    'base_value': 78.0,
                    'target_value': 85.0,
                    'trend': 'improving',
                    'variance': 3.0
                },
                'Smart meters streaming on AMI': {
                    'base_value': 0.01,
                    'target_value': 100.0,
                    'trend': 'improving',
                    'variance': 5.0
                },
                'Meters acquired and installed': {
                    'base_value': 0.0,
                    'target_value': 83000.0,
                    'trend': 'improving',
                    'variance': 5000.0
                },
                'MD Industrial Collection Efficiency': {
                    'base_value': 78.0,
                    'target_value': 90.0,
                    'trend': 'improving',
                    'variance': 3.0
                },
                'MD Non-Industrial Collection Efficiency': {
                    'base_value': 73.0,
                    'target_value': 80.0,
                    'trend': 'improving',
                    'variance': 2.0
                },
                'Regions Collection Efficiency': {
                    'base_value': 45.0,
                    'target_value': 60.0,
                    'trend': 'improving',
                    'variance': 3.0
                },
                'PPM collected revenue': {
                    'base_value': 500000000.0,
                    'target_value': 1000000000.0,
                    'trend': 'improving',
                    'variance': 50000000.0
                },
                'Mamuda monthly energy offtake': {
                    'base_value': 2.8,
                    'target_value': 3.0,
                    'trend': 'improving',
                    'variance': 0.1
                },
                'Top 20 MD customer attrition rate': {
                    'base_value': 0.0,
                    'target_value': 0.0,
                    'trend': 'stable',
                    'variance': 0.0
                },
                'Monthly Internally Generated Revenue': {
                    'base_value': 0.0,
                    'target_value': 105000000.0,
                    'trend': 'improving',
                    'variance': 5000000.0
                }
            },
            'CHRO': {
                'Monthly staff productivity per employee': {
                    'base_value': 3.1,
                    'target_value': 5.5,
                    'trend': 'improving',
                    'variance': 0.3
                },
                'C-Suite executive appraisals completed': {
                    'base_value': 0.0,
                    'target_value': 6.0,
                    'trend': 'improving',
                    'variance': 1.0
                },
                'Wage bill reduction achievement': {
                    'base_value': 0.0,
                    'target_value': 15.0,
                    'trend': 'improving',
                    'variance': 2.0
                }
            }
        }
        
        created_count = 0
        
        # Generate data for each month
        for month_offset in range(months, 0, -1):
            period_date = date.today().replace(day=1) - timedelta(days=month_offset * 30)
            period_date = period_date.replace(day=1)
            
            self.stdout.write(f"Creating data for {period_date.strftime('%B %Y')}")
            
            # For each executive role
            for role, kpis in sample_data.items():
                for kpi_name, config in kpis.items():
                    try:
                        # Get KPI definition
                        kpi_def = ExecutiveKPIDefinition.objects.get(
                            executive_role=role,
                            name=kpi_name,
                            is_active=True
                        )
                        
                        # Calculate progressive value based on trend
                        progress_ratio = (months - month_offset) / months  # 0 to 1
                        base = config['base_value']
                        target = config['target_value']
                        variance = config['variance']
                        trend = config['trend']
                        
                        if trend == 'improving':
                            # Progress from base towards target
                            value = base + (target - base) * progress_ratio * 0.7  # 70% progress over period
                        elif trend == 'declining':
                            # Progress away from target
                            value = base - (base * progress_ratio * 0.3)
                        else:  # stable
                            value = base
                        
                        # Add random variance
                        if variance > 0:
                            random_factor = 1 + random.uniform(-0.1, 0.1)  # ±10% random variation
                            value *= random_factor
                        
                        # Ensure value makes sense (no negatives for most KPIs)
                        if 'attrition' not in kpi_name.lower() and value < 0:
                            value = abs(value)
                        
                        # Create performance record
                        performance, created = ExecutivePerformance.objects.update_or_create(
                            kpi_definition=kpi_def,
                            period_date=period_date,
                            period_type='monthly',
                            defaults={
                                'actual_value': Decimal(str(round(value, 2))),
                                'data_source': 'sample_data',
                                'notes': f'Sample data for testing - {trend} trend',
                                'verified': True,
                                'created_by': creator_user
                            }
                        )
                        
                        if created:
                            created_count += 1
                        
                    except ExecutiveKPIDefinition.DoesNotExist:
                        self.stdout.write(
                            self.style.WARNING(f"KPI not found: {role} - {kpi_name}")
                        )
                        continue
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {created_count} performance records over {months} months"
            )
        )
        
        # Summary by role - Use the correct reference to ExecutiveRole choices
        self.stdout.write("\n📊 PERFORMANCE DATA SUMMARY:")
        
        # Use the ExecutiveRole.choices directly since it's defined as a separate class
        executive_roles = [
            ('CFO', 'Chief Financial Officer'),
            ('CTO', 'Chief Technology Officer'),
            ('CCO', 'Chief Commercial Officer'),
            ('CHRO', 'Chief Human Resources Officer')
        ]
        
        for role_code, role_name in executive_roles:
            count = ExecutivePerformance.objects.filter(
                kpi_definition__executive_role=role_code
            ).count()
            self.stdout.write(f"   • {role_code}: {count} performance records")
        
        self.stdout.write("\n🚀 Sample data created! You can now test the KPI APIs.")
        self.stdout.write("\nTest endpoints:")
        self.stdout.write("   • GET /hr/executive-kpis/cto/")
        self.stdout.write("   • GET /hr/executive-kpis/cco/")
        self.stdout.write("   • GET /hr/executive-kpis/cfo/")
        self.stdout.write("   • GET /hr/executive-kpis/chro/")
        self.stdout.write("   • GET /hr/executive-kpis/overview/")
        self.stdout.write("   • GET /hr/executive-kpis/alerts/")