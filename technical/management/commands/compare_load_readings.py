from django.core.management.base import BaseCommand
import mysql.connector
from datetime import datetime
from technical.models import HourlyLoad
from common.models import Feeder
from collections import defaultdict


class Command(BaseCommand):
    help = 'Compare hourly load readings between external and Raven databases (optimized)'

    def add_arguments(self, parser):
        parser.add_argument('--from-date', type=str, required=True, help='Start date (YYYY-MM-DD)')
        parser.add_argument('--to-date', type=str, required=True, help='End date (YYYY-MM-DD)')
        parser.add_argument('--feeders', type=str, help='Comma-separated feeder slugs')
        parser.add_argument('--output', type=str, help='Output CSV file')
        parser.add_argument('--batch-size', type=int, default=50, help='Number of feeders to process per batch')

    def handle(self, *args, **options):
        from_date = datetime.strptime(options['from_date'], '%Y-%m-%d').date()
        to_date = datetime.strptime(options['to_date'], '%Y-%m-%d').date()
        batch_size = options['batch_size']
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*70}'))
        self.stdout.write(self.style.SUCCESS(f'OPTIMIZED LOAD READING COMPARISON'))
        self.stdout.write(self.style.SUCCESS(f'{"="*70}'))
        self.stdout.write(f'Period: {from_date} to {to_date}')
        
        # Get feeders
        feeders = Feeder.objects.all()
        if options['feeders']:
            feeder_slugs = [slug.strip() for slug in options['feeders'].split(',')]
            feeders = feeders.filter(slug__in=feeder_slugs)
        
        feeder_list = list(feeders.values('id', 'name', 'slug'))
        self.stdout.write(f'Feeders to check: {len(feeder_list)}\n')
        
        if not feeder_list:
            self.stdout.write(self.style.ERROR('No feeders found!'))
            return
        
        # Create mapping for quick lookups
        slug_to_feeder = {f['slug']: f for f in feeder_list}
        feeder_id_to_info = {f['id']: f for f in feeder_list}
        
        total_checked = 0
        discrepancies = []
        feeders_checked = 0
        feeders_with_discrepancies = 0
        
        # Connect to external MySQL database
        try:
            connection = mysql.connector.connect(
                host='31.97.56.29',
                user='root',
                password='EMRC-Password-123#',
                database='dataNestDB_KEDCO',
                port=3306
            )
            cursor = connection.cursor(dictionary=True)
            self.stdout.write(self.style.SUCCESS('✓ Connected to external database'))
        except mysql.connector.Error as err:
            self.stdout.write(self.style.ERROR(f'Failed to connect to external database: {err}'))
            return
        
        try:
            # Process in batches to avoid memory issues
            for i in range(0, len(feeder_list), batch_size):
                batch = feeder_list[i:i + batch_size]
                batch_slugs = [f['slug'] for f in batch]
                batch_ids = [f['id'] for f in batch]
                
                self.stdout.write(f'Processing batch {i//batch_size + 1} ({len(batch)} feeders)...')
                
                # BULK QUERY 1: Get all external readings for this batch at once
                external_readings = defaultdict(dict)
                placeholders = ','.join(['%s'] * len(batch_slugs))
                query = f"""
                    SELECT feeder_id, Date, Hour_d, LoadS
                    FROM Technicalhourlydata
                    WHERE feeder_id IN ({placeholders})
                        AND Date BETWEEN %s AND %s
                    ORDER BY feeder_id, Date, Hour_d
                """
                cursor.execute(query, batch_slugs + [from_date, to_date])
                
                for row in cursor.fetchall():
                    feeder_slug = row['feeder_id']
                    date_val = row['Date']
                    hour_val = row['Hour_d']
                    load_val = row['LoadS']
                    
                    if load_val is None:
                        continue
                    
                    try:
                        load_mw = float(load_val)
                        external_readings[feeder_slug][(date_val, hour_val)] = load_mw
                    except (ValueError, TypeError):
                        continue
                
                # BULK QUERY 2: Get all Raven readings for this batch at once
                raven_readings = defaultdict(dict)
                raven_qs = HourlyLoad.objects.filter(
                    feeder_id__in=batch_ids,
                    date__range=(from_date, to_date)
                ).values('feeder_id', 'date', 'hour', 'load_mw')
                
                for r in raven_qs:
                    feeder_id = r['feeder_id']
                    feeder_slug = feeder_id_to_info[feeder_id]['slug']
                    raven_readings[feeder_slug][(r['date'], r['hour'])] = r['load_mw']
                
                # Compare readings for each feeder in batch
                for feeder in batch:
                    feeders_checked += 1
                    feeder_slug = feeder['slug']
                    feeder_name = feeder['name']
                    
                    ext_readings = external_readings.get(feeder_slug, {})
                    rav_readings = raven_readings.get(feeder_slug, {})
                    
                    # Get all unique (date, hour) combinations
                    all_keys = set(ext_readings.keys()) | set(rav_readings.keys())
                    feeder_disc_count = 0
                    
                    for date, hour in all_keys:
                        total_checked += 1
                        ext_val = ext_readings.get((date, hour))
                        rav_val = rav_readings.get((date, hour))
                        
                        # Check for discrepancies
                        if ext_val is None and rav_val is not None:
                            discrepancies.append({
                                'type': 'missing_external',
                                'feeder': feeder_name,
                                'feeder_slug': feeder_slug,
                                'date': date,
                                'hour': hour,
                                'external': None,
                                'raven': float(rav_val),
                                'difference': None,
                            })
                            feeder_disc_count += 1
                        elif rav_val is None and ext_val is not None:
                            discrepancies.append({
                                'type': 'missing_raven',
                                'feeder': feeder_name,
                                'feeder_slug': feeder_slug,
                                'date': date,
                                'hour': hour,
                                'external': float(ext_val),
                                'raven': None,
                                'difference': None,
                            })
                            feeder_disc_count += 1
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
                                    'feeder': feeder_name,
                                    'feeder_slug': feeder_slug,
                                    'date': date,
                                    'hour': hour,
                                    'external': ext_float,
                                    'raven': rav_float,
                                    'difference': rav_float - ext_float,
                                })
                                feeder_disc_count += 1
                    
                    if feeder_disc_count > 0:
                        feeders_with_discrepancies += 1
                
                self.stdout.write(self.style.SUCCESS(f'  ✓ Batch {i//batch_size + 1} complete'))
            
        finally:
            cursor.close()
            connection.close()
        
        # Print summary
        self.stdout.write(self.style.SUCCESS(f'\n{"="*70}'))
        self.stdout.write(self.style.SUCCESS('COMPARISON SUMMARY'))
        self.stdout.write(self.style.SUCCESS(f'{"="*70}'))
        self.stdout.write(f'Feeders checked: {feeders_checked}')
        self.stdout.write(f'Feeders with discrepancies: {feeders_with_discrepancies}')
        self.stdout.write(f'Total readings checked: {total_checked:,}')
        self.stdout.write(f'Discrepancies found: {len(discrepancies):,}')
        
        # Count by type
        by_type = defaultdict(int)
        for d in discrepancies:
            by_type[d['type']] += 1
        
        if by_type:
            self.stdout.write('\nBreakdown by type:')
            for disc_type in sorted(by_type.keys()):
                count = by_type[disc_type]
                self.stdout.write(f'  - {disc_type}: {count:,}')
        
        if total_checked > 0:
            accuracy = ((total_checked - len(discrepancies)) / total_checked * 100)
            self.stdout.write(f'\nAccuracy: {accuracy:.2f}%')
        
        # Show sample discrepancies
        if discrepancies:
            self.stdout.write(self.style.WARNING(f'\nSample discrepancies (first 20):'))
            for i, d in enumerate(discrepancies[:20], 1):
                self.stdout.write(f"\n{i}. {d['type']}")
                self.stdout.write(f"   Feeder: {d['feeder']} ({d['feeder_slug']})")
                self.stdout.write(f"   Date/Hour: {d['date']} {d['hour']:02d}:00")
                self.stdout.write(f"   External: {d['external']} MW")
                self.stdout.write(f"   Raven: {d['raven']} MW")
                if d.get('difference'):
                    self.stdout.write(f"   Difference: {d['difference']:+.2f} MW")
            
            if len(discrepancies) > 20:
                self.stdout.write(f"\n... and {len(discrepancies) - 20} more discrepancies")
        
        # Export to CSV if requested
        if options['output']:
            import csv
            with open(options['output'], 'w', newline='') as f:
                fieldnames = ['type', 'feeder', 'feeder_slug', 'date', 'hour', 'external', 'raven', 'difference']
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(discrepancies)
            self.stdout.write(self.style.SUCCESS(f'\nExported {len(discrepancies)} discrepancies to {options["output"]}'))