#!/usr/bin/env python
"""
Script to compare hourly load readings between external database and Raven database.
Identifies discrepancies where readings differ between the two systems.

Usage:
    python compare_load_readings.py --from-date 2025-01-01 --to-date 2025-01-31
    python compare_load_readings.py --from-date 2025-01-01 --to-date 2025-01-31 --feeders F001,F002
    python compare_load_readings.py --from-date 2025-01-01 --to-date 2025-01-31 --export csv
"""

import os
import sys
import django
import argparse
from datetime import datetime, date
from collections import defaultdict
import csv

# Setup Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connections
from technical.models import HourlyLoad
from common.models import Feeder


class LoadComparer:
    """Compare load readings between external and Raven databases"""
    
    def __init__(self, from_date, to_date, feeder_codes=None):
        self.from_date = self._parse_date(from_date)
        self.to_date = self._parse_date(to_date)
        self.feeder_codes = feeder_codes
        self.discrepancies = []
        self.stats = {
            'total_checked': 0,
            'discrepancies_found': 0,
            'external_missing': 0,
            'raven_missing': 0,
            'value_mismatch': 0,
            'external_nonzero_raven_zero': 0,
            'raven_nonzero_external_zero': 0,
        }
    
    def _parse_date(self, date_val):
        """Parse date string to date object"""
        if isinstance(date_val, str):
            return datetime.strptime(date_val, '%Y-%m-%d').date()
        elif isinstance(date_val, datetime):
            return date_val.date()
        return date_val
    
    def get_feeders(self):
        """Get feeders to compare"""
        queryset = Feeder.objects.all()
        
        if self.feeder_codes:
            queryset = queryset.filter(code__in=self.feeder_codes)
        
        return queryset
    
    def get_external_readings(self, feeder_code):
        """Get readings from external database for a feeder"""
        query = """
            SELECT 
                date,
                hour,
                load_mw
            FROM hourly_load
            WHERE feeder_code = %s
                AND date BETWEEN %s AND %s
            ORDER BY date, hour
        """
        
        with connections['external'].cursor() as cursor:
            cursor.execute(query, [feeder_code, self.from_date, self.to_date])
            rows = cursor.fetchall()
        
        # Store as dict with (date, hour) as key
        readings = {}
        for date, hour, load_mw in rows:
            readings[(date, hour)] = load_mw
        
        return readings
    
    def get_raven_readings(self, feeder_id):
        """Get readings from Raven database for a feeder"""
        readings_qs = HourlyLoad.objects.filter(
            feeder_id=feeder_id,
            date__range=(self.from_date, self.to_date)
        ).values('date', 'hour', 'load_mw').order_by('date', 'hour')
        
        # Store as dict with (date, hour) as key
        readings = {}
        for reading in readings_qs:
            readings[(reading['date'], reading['hour'])] = reading['load_mw']
        
        return readings
    
    def compare_feeder(self, feeder):
        """Compare readings for a single feeder"""
        print(f"\nComparing: {feeder.name} ({feeder.code})")
        
        # Get readings from both databases
        external_readings = self.get_external_readings(feeder.code)
        raven_readings = self.get_raven_readings(feeder.id)
        
        # Get all unique (date, hour) combinations
        all_keys = set(external_readings.keys()) | set(raven_readings.keys())
        
        feeder_discrepancies = []
        
        for date, hour in sorted(all_keys):
            self.stats['total_checked'] += 1
            
            external_value = external_readings.get((date, hour))
            raven_value = raven_readings.get((date, hour))
            
            discrepancy = None
            
            # Case 1: Reading exists in external but not in Raven
            if external_value is not None and raven_value is None:
                discrepancy = {
                    'type': 'raven_missing',
                    'feeder_name': feeder.name,
                    'feeder_code': feeder.code,
                    'date': date,
                    'hour': hour,
                    'external_value': float(external_value),
                    'raven_value': None,
                    'difference': None,
                }
                self.stats['raven_missing'] += 1
            
            # Case 2: Reading exists in Raven but not in external
            elif raven_value is not None and external_value is None:
                discrepancy = {
                    'type': 'external_missing',
                    'feeder_name': feeder.name,
                    'feeder_code': feeder.code,
                    'date': date,
                    'hour': hour,
                    'external_value': None,
                    'raven_value': float(raven_value),
                    'difference': None,
                }
                self.stats['external_missing'] += 1
            
            # Case 3: Reading exists in both but values differ
            elif external_value is not None and raven_value is not None:
                external_float = float(external_value)
                raven_float = float(raven_value)
                
                # Allow small floating point differences (0.01 MW tolerance)
                if abs(external_float - raven_float) > 0.01:
                    difference = raven_float - external_float
                    
                    # Special case: External has value but Raven is zero
                    if external_float > 0 and raven_float == 0:
                        discrepancy_type = 'external_nonzero_raven_zero'
                        self.stats['external_nonzero_raven_zero'] += 1
                    # Special case: Raven has value but external is zero
                    elif raven_float > 0 and external_float == 0:
                        discrepancy_type = 'raven_nonzero_external_zero'
                        self.stats['raven_nonzero_external_zero'] += 1
                    else:
                        discrepancy_type = 'value_mismatch'
                        self.stats['value_mismatch'] += 1
                    
                    discrepancy = {
                        'type': discrepancy_type,
                        'feeder_name': feeder.name,
                        'feeder_code': feeder.code,
                        'date': date,
                        'hour': hour,
                        'external_value': external_float,
                        'raven_value': raven_float,
                        'difference': difference,
                    }
            
            if discrepancy:
                feeder_discrepancies.append(discrepancy)
                self.discrepancies.append(discrepancy)
                self.stats['discrepancies_found'] += 1
        
        print(f"  Found {len(feeder_discrepancies)} discrepancies")
        return feeder_discrepancies
    
    def run_comparison(self):
        """Run comparison for all feeders"""
        print(f"\n{'='*70}")
        print(f"LOAD READING COMPARISON")
        print(f"{'='*70}")
        print(f"Period: {self.from_date} to {self.to_date}")
        
        feeders = self.get_feeders()
        print(f"Feeders to check: {feeders.count()}")
        
        for feeder in feeders:
            try:
                self.compare_feeder(feeder)
            except Exception as e:
                print(f"  ERROR comparing {feeder.name}: {str(e)}")
        
        self.print_summary()
    
    def print_summary(self):
        """Print summary of comparison results"""
        print(f"\n{'='*70}")
        print(f"COMPARISON SUMMARY")
        print(f"{'='*70}")
        print(f"Total readings checked: {self.stats['total_checked']:,}")
        print(f"Discrepancies found: {self.stats['discrepancies_found']:,}")
        print(f"\nBreakdown by type:")
        print(f"  - Missing in Raven: {self.stats['raven_missing']:,}")
        print(f"  - Missing in External: {self.stats['external_missing']:,}")
        print(f"  - Value mismatch: {self.stats['value_mismatch']:,}")
        print(f"  - External non-zero, Raven zero: {self.stats['external_nonzero_raven_zero']:,}")
        print(f"  - Raven non-zero, External zero: {self.stats['raven_nonzero_external_zero']:,}")
        
        if self.discrepancies:
            accuracy = ((self.stats['total_checked'] - self.stats['discrepancies_found']) 
                       / self.stats['total_checked'] * 100)
            print(f"\nAccuracy: {accuracy:.2f}%")
    
    def print_detailed_report(self, limit=50):
        """Print detailed report of discrepancies"""
        if not self.discrepancies:
            print("\nNo discrepancies found!")
            return
        
        print(f"\n{'='*70}")
        print(f"DETAILED DISCREPANCIES (showing first {limit})")
        print(f"{'='*70}")
        
        for i, disc in enumerate(self.discrepancies[:limit], 1):
            print(f"\n{i}. {disc['type'].upper()}")
            print(f"   Feeder: {disc['feeder_name']} ({disc['feeder_code']})")
            print(f"   Date/Hour: {disc['date']} {disc['hour']:02d}:00")
            print(f"   External: {disc['external_value']} MW")
            print(f"   Raven: {disc['raven_value']} MW")
            if disc['difference'] is not None:
                print(f"   Difference: {disc['difference']:+.2f} MW")
        
        if len(self.discrepancies) > limit:
            print(f"\n... and {len(self.discrepancies) - limit} more discrepancies")
    
    def export_to_csv(self, filename='load_discrepancies.csv'):
        """Export discrepancies to CSV file"""
        if not self.discrepancies:
            print("No discrepancies to export!")
            return
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'type', 'feeder_name', 'feeder_code', 'date', 'hour',
                'external_value', 'raven_value', 'difference'
            ])
            writer.writeheader()
            writer.writerows(self.discrepancies)
        
        print(f"\nExported {len(self.discrepancies)} discrepancies to {filename}")
    
    def export_to_excel(self, filename='load_discrepancies.xlsx'):
        """Export discrepancies to Excel file"""
        try:
            import pandas as pd
            
            if not self.discrepancies:
                print("No discrepancies to export!")
                return
            
            df = pd.DataFrame(self.discrepancies)
            
            # Create Excel writer
            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                # Summary sheet
                summary_df = pd.DataFrame([
                    {'Metric': 'Total Readings Checked', 'Count': self.stats['total_checked']},
                    {'Metric': 'Discrepancies Found', 'Count': self.stats['discrepancies_found']},
                    {'Metric': 'Missing in Raven', 'Count': self.stats['raven_missing']},
                    {'Metric': 'Missing in External', 'Count': self.stats['external_missing']},
                    {'Metric': 'Value Mismatch', 'Count': self.stats['value_mismatch']},
                    {'Metric': 'External Non-Zero, Raven Zero', 'Count': self.stats['external_nonzero_raven_zero']},
                    {'Metric': 'Raven Non-Zero, External Zero', 'Count': self.stats['raven_nonzero_external_zero']},
                ])
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
                
                # Discrepancies sheet
                df.to_excel(writer, sheet_name='Discrepancies', index=False)
            
            print(f"\nExported {len(self.discrepancies)} discrepancies to {filename}")
        
        except ImportError:
            print("pandas not installed. Using CSV export instead.")
            self.export_to_csv(filename.replace('.xlsx', '.csv'))


def main():
    parser = argparse.ArgumentParser(
        description='Compare hourly load readings between external and Raven databases'
    )
    parser.add_argument(
        '--from-date',
        required=True,
        help='Start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--to-date',
        required=True,
        help='End date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--feeders',
        help='Comma-separated list of feeder codes to check (default: all feeders)'
    )
    parser.add_argument(
        '--export',
        choices=['csv', 'excel'],
        help='Export results to file'
    )
    parser.add_argument(
        '--output',
        help='Output filename (default: load_discrepancies.csv or .xlsx)'
    )
    parser.add_argument(
        '--detailed',
        action='store_true',
        help='Show detailed report of discrepancies'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=50,
        help='Limit number of detailed discrepancies to show (default: 50)'
    )
    
    args = parser.parse_args()
    
    # Parse feeder codes
    feeder_codes = None
    if args.feeders:
        feeder_codes = [code.strip() for code in args.feeders.split(',')]
    
    # Run comparison
    comparer = LoadComparer(args.from_date, args.to_date, feeder_codes)
    comparer.run_comparison()
    
    # Show detailed report if requested
    if args.detailed:
        comparer.print_detailed_report(limit=args.limit)
    
    # Export if requested
    if args.export:
        if args.export == 'csv':
            filename = args.output or 'load_discrepancies.csv'
            comparer.export_to_csv(filename)
        elif args.export == 'excel':
            filename = args.output or 'load_discrepancies.xlsx'
            comparer.export_to_excel(filename)


if __name__ == '__main__':
    main()