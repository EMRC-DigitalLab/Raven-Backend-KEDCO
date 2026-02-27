import os
import django
from django.db import connections

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

def test_connection():
    try:
        print("Connecting to 'external' database...")
        with connections['external'].cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            print(f"Connection successful: {row}")
            
            # List tables to verify
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            print("\nTables in DataNest:")
            for table in tables:
                print(f" - {table[0]}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")

if __name__ == "__main__":
    test_connection()
