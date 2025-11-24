from django.core.management.base import BaseCommand
from django.db import connections
from datetime import datetime
from technical.models import HourlyLoad
from common.models import Feeder


class Command(BaseCommand):
    help = 'Compare hourly load readings between external and Raven databases'

    def add_arguments(self, parser):
        parser.add_argument('--from-date', type=str, required=True, help='Start date (YYYY-MM-DD)')
        parser.add_argument('--to-date', type=str, required=True, help='End date (YYYY-MM-DD)')
        parser.add_argument('--feeders', type=str, help='Comma-separated feeder codes')
        parser.add_argument('--output', type=str, help='Output CSV file')

    def handle(self, *args, **options):
        from_date = datetime.strptime(options['from_date'], '%Y-%m-%d').date()
        to_date = datetime.strptime(options['to_date'], '%Y-%m-%d').date()
        
        self.stdout.write(self.style.SUCCESS(f'\nComparing load readings from {from_date} to {to_date}\n'))
        
        # Get feeders
        feeders = Feeder.objects.all()
        if options['feeders']:
            feeder_codes = [code.strip() for code in options['feeders'].split(',')]
            feeders = feeders.filter(code__in=feeder_codes)
        
        total_checked = 0
        discrepancies = []
        
        for feeder in feeders:
            self.stdout.write(f'Checking {feeder.name} ({feeder.code})...')
            
            # Get external readings
            with connections['external'].cursor() as cursor:
                cursor.execute("""
                    SELECT date, hour, load_mw
                    FROM hourly_load
                    WHERE feeder_code = %s AND date BETWEEN %s AND %s
                    ORDER BY date, hour
                """, [feeder.code, from_date, to_date])
                external_readings = {(row[0], row[1]): row[2] for row in cursor.fetchall()}
            
            # Get Raven readings
            raven_qs = HourlyLoad.objects.filter(
                feeder=feeder,
                date__range=(from_date, to_date)
            ).values('date', 'hour', 'load_mw')
            raven_readings = {(r['date'], r['hour']): r['load_mw'] for r in raven_qs}
            
            # Compare
            all_keys = set(external_readings.keys()) | set(raven_readings.keys())
            
            for date, hour in sorted(all_keys):
                total_checked += 1
                ext_val = external_readings.get((date, hour))
                rav_val = raven_readings.get((date, hour))
                
                # Check for discrepancies
                if ext_val is None and rav_val is not None:
                    discrepancies.append({
                        'type': 'missing_external',
                        'feeder': feeder.name,
                        'code': feeder.code,
                        'date': date,
                        'hour': hour,
                        'external': None,
                        'raven': float(rav_val),
                    })
                elif rav_val is None and ext_val is not None:
                    discrepancies.append({
                        'type': 'missing_raven',
                        'feeder': feeder.name,
                        'code': feeder.code,
                        'date': date,
                        'hour': hour,
                        'external': float(ext_val),
                        'raven': None,
                    })
                elif ext_val is not None and rav_val is not None:
                    ext_float = float(ext_val)
                    rav_float = float(rav_val)
                    
                    if abs(ext_float - rav_float) > 0.01:
                        disc_type = 'value_mismatch'
                        if ext_float > 0 and rav_float == 0:
                            disc_type = 'external_nonzero_raven_zero'
                        elif rav_float > 0 and ext_float == 0:
                            disc_type = 'raven_nonzero_external_zero'
                        
                        discrepancies.append({
                            'type': disc_type,
                            'feeder': feeder.name,
                            'code': feeder.code,
                            'date': date,
                            'hour': hour,
                            'external': ext_float,
                            'raven': rav_float,
                        })
        
        # Print summary
        self.stdout.write(self.style.SUCCESS(f'\n{"="*70}'))
        self.stdout.write(self.style.SUCCESS('COMPARISON SUMMARY'))
        self.stdout.write(self.style.SUCCESS(f'{"="*70}'))
        self.stdout.write(f'Total readings checked: {total_checked:,}')
        self.stdout.write(f'Discrepancies found: {len(discrepancies):,}')
        
        # Count by type
        by_type = {}
        for d in discrepancies:
            by_type[d['type']] = by_type.get(d['type'], 0) + 1
        
        if by_type:
            self.stdout.write('\nBreakdown by type:')
            for disc_type, count in by_type.items():
                self.stdout.write(f'  - {disc_type}: {count:,}')
        
        # Show sample discrepancies
        if discrepancies:
            self.stdout.write(self.style.WARNING(f'\nSample discrepancies (first 10):'))
            for i, d in enumerate(discrepancies[:10], 1):
                self.stdout.write(f"\n{i}. {d['type']}")
                self.stdout.write(f"   Feeder: {d['feeder']} ({d['code']})")
                self.stdout.write(f"   Date/Hour: {d['date']} {d['hour']:02d}:00")
                self.stdout.write(f"   External: {d['external']} MW")
                self.stdout.write(f"   Raven: {d['raven']} MW")
        
        # Export to CSV if requested
        if options['output']:
            import csv
            with open(options['output'], 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['type', 'feeder', 'code', 'date', 'hour', 'external', 'raven'])
                writer.writeheader()
                writer.writerows(discrepancies)
            self.stdout.write(self.style.SUCCESS(f'\nExported to {options["output"]}'))