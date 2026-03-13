# users/management/commands/setup_sections.py
from django.core.management.base import BaseCommand

from users.models import Permission, Section


class Command(BaseCommand):
    help = 'Setup default sections and permissions'
    
    def handle(self, *args, **options):
        # Create sections
        sections_data = [
            {'name': 'overview', 'display_name': 'Overview', 'icon': 'IconAperture', 
             'description': 'Dashboard overview and general metrics'},
            {'name': 'commercial', 'display_name': 'Commercial', 'icon': 'IconChartDonut3', 
             'description': 'Commercial operations and customer management'},
            {'name': 'financial', 'display_name': 'Financial', 'icon': 'IconCurrencyDollar', 
             'description': 'Financial management and accounting'},
            {'name': 'technical', 'display_name': 'Technical', 'icon': 'IconSettings', 
             'description': 'Technical operations and maintenance'},
            {'name': 'hr', 'display_name': 'Human Resource', 'icon': 'IconApps', 
             'description': 'Human resource management'},
            {'name': 'regulatory', 'display_name': 'Regulatory', 'icon': 'IconFileDescription', 
             'description': 'Regulatory compliance and reporting'},
        ]
        
        for section_data in sections_data:
            section, created = Section.objects.get_or_create(
                name=section_data['name'],
                defaults=section_data
            )
            if created:
                self.stdout.write(f"Created section: {section.display_name}")
                
                # Create default permissions for each section
                permissions_data = [
                    {'name': 'View', 'codename': f'view_{section.name}', 'permission_type': 'view'},
                    {'name': 'Create', 'codename': f'create_{section.name}', 'permission_type': 'create'},
                    {'name': 'Edit', 'codename': f'edit_{section.name}', 'permission_type': 'edit'},
                    {'name': 'Delete', 'codename': f'delete_{section.name}', 'permission_type': 'delete'},
                    {'name': 'Admin', 'codename': f'admin_{section.name}', 'permission_type': 'admin'},
                ]
                
                for perm_data in permissions_data:
                    perm_data['section'] = section
                    permission, created = Permission.objects.get_or_create(
                        codename=perm_data['codename'],
                        defaults=perm_data
                    )
                    if created:
                        self.stdout.write(f"  Created permission: {permission.name}")
            else:
                self.stdout.write(f"Section already exists: {section.display_name}")
        
        self.stdout.write(self.style.SUCCESS('Successfully setup sections and permissions'))