# management/commands/populate_hq_opex.py
import pymysql
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils.text import slugify
from decouple import config
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
import logging

from financial.models import HQOpex, OpexCategory, GLBreakdown

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Populate HQ OPEX data from legacy MySQL database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without making changes to the database'
        )
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for filtering (YYYY-MM-DD format)'
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for filtering (YYYY-MM-DD format)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to process in each batch (default: 1000)'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip records that already exist based on date and purpose'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        self.skip_existing = options['skip_existing']
        
        if self.dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No changes will be made to the database')
            )
        
    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        self.skip_existing = options['skip_existing']
        
        if self.dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No changes will be made to the database')
            )
        
        # Connection configuration for stability
        connection_config = {
            'host': config("legacy_mysql_server"),
            'user': config("legacy_user"),
            'password': config("legacy_password"),
            'db': config("legacy_db"),
            'cursorclass': pymysql.cursors.DictCursor,
            'connect_timeout': 60,
            'read_timeout': 300,
            'write_timeout': 300,
            'charset': 'utf8mb4',
            'autocommit': True,
        }
        
        conn = None
        try:
            # Get total count first
            total_records = self._get_total_count(connection_config, options)
            self.stdout.write(f"Found {total_records} total records to process")
            
            # Process records in chunks using LIMIT/OFFSET
            processed_count = 0
            created_count = 0
            skipped_count = 0
            error_count = 0
            created_categories = set()
            created_breakdowns = set()
            
            offset = 0
            
            while offset < total_records:
                # Create fresh connection for each batch to avoid timeouts
                conn = self._create_connection(connection_config)
                
                try:
                    batch_records = self._fetch_batch(conn, options, offset, self.batch_size)
                    
                    if not batch_records:
                        break
                    
                    self.stdout.write(f"Processing batch {offset//self.batch_size + 1}: records {offset+1} to {min(offset+len(batch_records), total_records)}")
                    
                    for record in batch_records:
                        try:
                            result = self._process_record(record)
                            if result == 'created':
                                created_count += 1
                            elif result == 'skipped':
                                skipped_count += 1
                            
                            # Track new categories and breakdowns
                            if record.get('opex categorization'):
                                created_categories.add(record['opex categorization'])
                            if record.get('GL Account opex break down'):
                                created_breakdowns.add(record['GL Account opex break down'])
                                
                            processed_count += 1
                            
                        except Exception as e:
                            error_count += 1
                            logger.error(f"Error processing record {record.get('Date', 'Unknown')}: {str(e)}")
                            self.stdout.write(
                                self.style.ERROR(f"Error processing record: {str(e)}")
                            )
                    
                    offset += len(batch_records)
                    
                except Exception as e:
                    logger.error(f"Error processing batch at offset {offset}: {str(e)}")
                    error_count += len(batch_records) if 'batch_records' in locals() else self.batch_size
                    offset += self.batch_size
                    
                finally:
                    if conn:
                        conn.close()
                        conn = None
                
                # Small delay between batches to be gentle on the database
                import time
                time.sleep(0.1)
            
            # Print summary
            self._print_summary(processed_count, created_count, skipped_count, error_count, 
                              created_categories, created_breakdowns)
                
        except Exception as e:
            raise CommandError(f"Unexpected error: {str(e)}")
        finally:
            if conn:
                conn.close()
                self.stdout.write("Database connection closed")
    
    def _create_connection(self, config):
        """Create a new MySQL connection with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                conn = pymysql.connect(**config)
                # Test the connection
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                return conn
            except pymysql.Error as e:
                if attempt == max_retries - 1:
                    raise CommandError(f"Failed to connect after {max_retries} attempts: {str(e)}")
                self.stdout.write(f"Connection attempt {attempt + 1} failed, retrying...")
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
    
    def _get_total_count(self, connection_config, options):
        """Get total record count"""
        conn = self._create_connection(connection_config)
        try:
            query = "SELECT COUNT(*) as total FROM hq_financialopex"
            params = []
            
            if options['start_date'] or options['end_date']:
                conditions = []
                if options['start_date']:
                    conditions.append("Date >= %s")
                    params.append(options['start_date'])
                if options['end_date']:
                    conditions.append("Date <= %s")
                    params.append(options['end_date'])
                query += " WHERE " + " AND ".join(conditions)
            
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                result = cursor.fetchone()
                return result['total'] if result else 0
        finally:
            conn.close()
    
    def _fetch_batch(self, conn, options, offset, limit):
        """Fetch a batch of records using LIMIT/OFFSET"""
        query = "SELECT * FROM hq_financialopex"
        params = []
        
        if options['start_date'] or options['end_date']:
            conditions = []
            if options['start_date']:
                conditions.append("Date >= %s")
                params.append(options['start_date'])
            if options['end_date']:
                conditions.append("Date <= %s")
                params.append(options['end_date'])
            query += " WHERE " + " AND ".join(conditions)
        
        query += f" ORDER BY Date LIMIT {limit} OFFSET {offset}"
        
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def _print_summary(self, processed_count, created_count, skipped_count, error_count, 
                       created_categories, created_breakdowns):
        """Print migration summary"""
        self.stdout.write("\n" + "="*50)
        self.stdout.write(self.style.SUCCESS("MIGRATION SUMMARY"))
        self.stdout.write("="*50)
        self.stdout.write(f"Total records processed: {processed_count}")
        self.stdout.write(f"Records created: {created_count}")
        self.stdout.write(f"Records skipped: {skipped_count}")
        self.stdout.write(f"Errors encountered: {error_count}")
        self.stdout.write(f"New OPEX categories created: {len(created_categories)}")
        self.stdout.write(f"New GL breakdowns created: {len(created_breakdowns)}")
        
        if created_categories:
            self.stdout.write("\nNew OPEX Categories:")
            for category in sorted(created_categories):
                self.stdout.write(f"  - {category}")
        
        if created_breakdowns:
            self.stdout.write("\nNew GL Breakdowns:")
            for breakdown in sorted(created_breakdowns):
                self.stdout.write(f"  - {breakdown}")

    def _process_record(self, record):
        """Process a single record from the legacy database"""
        try:
            # Parse and validate date
            date_obj = self._parse_date(record.get('Date'))
            if not date_obj:
                logger.warning(f"Invalid or missing date: {record.get('Date')}")
                return 'skipped'
            
            # Clean and validate purpose
            purpose = self._clean_text(record.get('Purpose of transaction', ''))
            if not purpose:
                logger.warning(f"Missing purpose for record dated {date_obj}")
                purpose = 'Unknown Transaction'
            
            # Clean payee
            payee = self._clean_text(record.get('Payment to', ''))
            if not payee:
                payee = 'Unknown Payee'
            
            # Parse monetary values
            debit = self._parse_decimal(record.get('Debit', 0))
            credit = self._parse_decimal(record.get('Credit', 0))
            
            # FIXED: Use GL Account code for gl_account_number
            gl_account_number = self._clean_text(record.get('GL Account code number', ''))

            
            # FIXED: Handle OPEX categorization - create if doesn't exist
            opex_category = None
            opex_category_name = self._clean_text(record.get('opex categorization', ''))
            if opex_category_name:
                opex_category = self._get_or_create_opex_category(opex_category_name)
            
            # FIXED: Handle GL breakdown - create if doesn't exist
            gl_breakdown = None
            gl_breakdown_name = self._clean_text(record.get('GL Account opex break down', ''))
            if gl_breakdown_name:
                gl_breakdown = self._get_or_create_gl_breakdown(gl_breakdown_name)
                
            # Debug logging
            if gl_breakdown_name and not self.dry_run:
                logger.info(f"GL Breakdown: '{gl_breakdown_name}' -> {gl_breakdown}")
            
            # Check if record already exists (if skip_existing is enabled)
            if self.skip_existing:
                existing = HQOpex.objects.filter(
                    date=date_obj,
                    purpose__iexact=purpose
                ).first()
                
                if existing:
                    return 'skipped'
            
            # Create the HQ OPEX record
            if not self.dry_run:
                hq_opex = HQOpex.objects.create(
                    date=date_obj,
                    purpose=purpose,
                    payee=payee,
                    gl_account_number=gl_account_number,
                    gl_breakdown=gl_breakdown,
                    opex_category=opex_category,
                    debit=debit,
                    credit=credit
                )
                
                logger.info(f"Created HQ OPEX record: {hq_opex} | GL Breakdown: {gl_breakdown}")
            else:
                logger.info(f"Would create HQ OPEX: {date_obj} - {purpose} - ₦{credit} | GL Code: {gl_account_number} | GL Breakdown: {gl_breakdown_name}")
            
            return 'created'
            
        except Exception as e:
            logger.error(f"Error processing record: {str(e)}")
            logger.error(f"Record data: {record}")
            raise

    def _parse_date(self, date_value):
        """Parse date from various formats"""
        if not date_value:
            return None
        
        # If it's already a date object
        if isinstance(date_value, date):
            return date_value
        
        # If it's a string, try various formats
        if isinstance(date_value, str):
            date_formats = [
                '%Y-%m-%d',
                '%d/%m/%Y',
                '%m/%d/%Y',
                '%Y-%m-%d %H:%M:%S',
                '%d-%m-%Y',
                '%d.%m.%Y'
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_value.strip(), fmt).date()
                    return parsed_date
                except ValueError:
                    continue
        
        logger.warning(f"Could not parse date: {date_value}")
        return None

    def _parse_decimal(self, value):
        """Parse decimal value from various formats"""
        if value is None or value == '':
            return Decimal('0.00')
        
        try:
            # Remove currency symbols and commas
            if isinstance(value, str):
                value = value.replace('₦', '').replace('$', '').replace(',', '').strip()
                if not value:
                    return Decimal('0.00')
            
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            logger.warning(f"Could not parse decimal value: {value}")
            return Decimal('0.00')

    def _clean_text(self, text):
        """Clean and normalize text fields"""
        if not text:
            return ''
        
        # Convert to string and strip whitespace
        cleaned = str(text).strip()
        
        # Remove null bytes and other problematic characters
        cleaned = cleaned.replace('\x00', '').replace('\r', '').replace('\n', ' ')
        
        # Normalize whitespace
        cleaned = ' '.join(cleaned.split())
        
        return cleaned

    def _get_or_create_gl_breakdown(self, breakdown_name):
        """Get or create GL breakdown - FIXED to ensure creation"""
        if not breakdown_name:
            return None
        
        # Clean the breakdown name
        breakdown_name = breakdown_name.strip()
        if not breakdown_name:
            return None
        
        try:
            # Try to get existing breakdown (case-insensitive)
            breakdown = GLBreakdown.objects.filter(name__iexact=breakdown_name).first()
            
            if not breakdown:
                if not self.dry_run:
                    breakdown = GLBreakdown.objects.create(name=breakdown_name)
                    logger.info(f"Created new GL breakdown: '{breakdown_name}' -> ID: {breakdown.id}")
                    self.stdout.write(f"  ✓ Created GL Breakdown: {breakdown_name}")
                else:
                    logger.info(f"Would create GL breakdown: '{breakdown_name}'")
                    return None  # Return None in dry-run mode
            else:
                logger.debug(f"Found existing GL breakdown: '{breakdown_name}' -> ID: {breakdown.id}")
            
            return breakdown
            
        except Exception as e:
            logger.error(f"Error creating GL breakdown '{breakdown_name}': {str(e)}")
            # Don't return None here, re-raise the exception to see what's wrong
            raise

    def _get_or_create_opex_category(self, category_name):
        """Get or create OPEX category - FIXED to ensure creation"""
        if not category_name:
            return None
        
        # Clean the category name
        category_name = category_name.strip()
        if not category_name:
            return None
        
        try:
            # Try to get existing category (case-insensitive)
            category = OpexCategory.objects.filter(name__iexact=category_name).first()
            
            if not category:
                if not self.dry_run:
                    category = OpexCategory.objects.create(name=category_name)
                    logger.info(f"Created new OPEX category: '{category_name}' -> ID: {category.id}")
                    self.stdout.write(f"  ✓ Created OPEX Category: {category_name}")
                else:
                    logger.info(f"Would create OPEX category: '{category_name}'")
                    return None  # Return None in dry-run mode
            else:
                logger.debug(f"Found existing OPEX category: '{category_name}' -> ID: {category.id}")
            
            return category
            
        except Exception as e:
            logger.error(f"Error creating OPEX category '{category_name}': {str(e)}")
            # Don't return None here, re-raise the exception to see what's wrong
            raise