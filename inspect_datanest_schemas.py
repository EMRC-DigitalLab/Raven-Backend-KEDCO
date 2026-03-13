import os

import django
from django.db import connections

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

def inspect_schemas():
    conn = connections['external']
    cursor = conn.cursor()
    tables = [
        'states', 'business_districts', 'injection_stations', 
        'feeders', 'transformers', 'feeder_transformers', 
        'feeder_supply_relationships'
    ]
    
    for table in tables:
        print(f"\n=== Schema: {table} ===")
        try:
            cursor.execute(f"DESCRIBE {table}")
            for row in cursor.fetchall():
                print(row)
        except Exception as e:
            print(f"Error describing {table}: {e}")

if __name__ == "__main__":
    inspect_schemas()
