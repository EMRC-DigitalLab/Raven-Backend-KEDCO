import os
import sys

import django
from django.db import connections, transaction
from django.utils.text import slugify

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The script is in technical/scripts/sync_datanest_mapping.py
# parent is technical/scripts
# parent.parent is technical
# we need one more level up for raven root
PROJECT_ROOT = os.path.dirname(PROJECT_ROOT)

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

from common.models import (
    BusinessDistrict,
    Feeder,
    FeederSupplyRelationship,
    FeederTransformerMapping,
    InjectionSubstation,
    PowerTransformer,
    State,
)


def get_or_update_by_mapping(model_class, slug, name, **defaults):
    """
    Helper to prevent IntegrityError on Unique names and handle duplicates.
    1. Prefer matching by slug (canonical ID).
    2. If slug doesn't exist but name does, try to reconcile.
    3. If name collision occurs and slug is taken, deactivate the redundant record.
    """
    try:
        # 1. Check if slug already exists
        obj_by_slug = model_class.objects.filter(slug=slug).first()
        if obj_by_slug:
            # Found canonical record, just update it
            for key, value in defaults.items():
                setattr(obj_by_slug, key, value)
            obj_by_slug.name = name
            obj_by_slug.save()
            return obj_by_slug, False

        # 2. Slug doesn't exist. Check if name exists as a "pairing" problem or duplicate.
        # Note: Some models might have multiple records with same name but different slugs (e.g. DUTSE)
        existing_names = list(model_class.objects.filter(name=name))
        if existing_names:
            # We found records with this name but different slugs
            # Since we didn't find the target slug 'slug' above, we can try to repurpose one
            target_obj = existing_names[0]
            print(f"   ! Reconciling: '{name}' name-match found. Repurposing {target_obj.slug} -> {slug}", flush=True)
            
            # Special case: check if we have multiple duplicates and deactivate others
            if len(existing_names) > 1:
                for other in existing_names[1:]:
                    print(f"   ! Cleaning duplicate: Deactivating {other.slug} ({other.name})", flush=True)
                    if hasattr(other, 'status'):
                        other.status = 'inactive'
                        other.save()

            target_obj.slug = slug
            for key, value in defaults.items():
                setattr(target_obj, key, value)
            target_obj.save()
            return target_obj, False
        
        # 3. Neither slug nor name exists, create fresh
        obj = model_class.objects.create(
            slug=slug,
            name=name,
            **defaults
        )
        return obj, True
    except Exception as e:
        print(f"   X Error syncing {model_class.__name__} {slug}/{name}: {e}", flush=True)
        return None, False

def sync_mappings():
    print("--- Starting DataNest to Raven Mapping Sync ---", flush=True)
    
    # Connect to external DataNest DB
    external_conn = connections['external']
    cursor = external_conn.cursor()

    # 1. Sync States
    print("\nSyncing States...", flush=True)
    cursor.execute("SELECT state_id, state_name FROM states")
    for state_id, state_name in cursor.fetchall():
        get_or_update_by_mapping(State, state_id, state_name)

    # 2. Sync Business Districts
    print("\nSyncing Business Districts...", flush=True)
    cursor.execute("SELECT district_id, district_name, state_id FROM business_districts")
    for district_id, district_name, state_id in cursor.fetchall():
        state = State.objects.filter(slug=state_id).first()
        if state:
            get_or_update_by_mapping(BusinessDistrict, district_id, district_name, state=state)

    # 3. Sync Injection Substations
    print("\nSyncing Injection Substations...", flush=True)
    cursor.execute("SELECT injection_station_id, injection_station_name, state_id, station_type FROM injection_stations")
    for is_id, is_name, state_id, st_type in cursor.fetchall():
        state = State.objects.filter(slug=state_id).first()
        if state:
            get_or_update_by_mapping(
                InjectionSubstation, is_id, is_name, 
                state=state, station_type=st_type, status='active'
            )

    # 4. Sync Feeders
    print("\nSyncing Feeders...", flush=True)
    cursor.execute("SELECT feeder_id, feeder_name, injection_station_id, district_id, feeder_class FROM feeders")
    for f_id, f_name, is_id, dist_id, f_class in cursor.fetchall():
        substation = InjectionSubstation.objects.filter(slug=is_id).first()
        district = BusinessDistrict.objects.filter(slug=dist_id).first() if dist_id else None
        
        if substation:
            get_or_update_by_mapping(
                Feeder, f_id, f_name,
                substation=substation,
                business_district=district,
                feeder_class=f_class or '',
                status='active'
            )

    # 5. Sync Power Transformers
    print("\nSyncing Power Transformers...", flush=True)
    cursor.execute("SELECT transformer_id, transformer_name, capacity_mva, voltage_rating, manufacturer, installation_year, status FROM transformers")
    for t_id, t_name, cap, volt, manu, year, status in cursor.fetchall():
        get_or_update_by_mapping(
            PowerTransformer, t_id, t_name,
            capacity_mva=cap,
            voltage_rating=volt or '33/11kV',
            manufacturer=manu or '',
            installation_year=year,
            status=status
        )

    # 6. Sync Feeder-Transformer Mappings
    print("\nSyncing Feeder-Transformer Mappings...", flush=True)
    cursor.execute("SELECT feeder_id, transformer_id, connection_type, status FROM feeder_transformers")
    FeederTransformerMapping.objects.all().delete()
    for f_id, t_id, conn_type, status in cursor.fetchall():
        try:
            feeder = Feeder.objects.filter(slug=f_id).first()
            transformer = PowerTransformer.objects.filter(slug=t_id).first()
            if feeder and transformer:
                FeederTransformerMapping.objects.create(
                    feeder=feeder,
                    transformer=transformer,
                    connection_type=conn_type or '',
                    status=status
                )
        except Exception as e:
            print(f"   ! Error mapping {f_id} to {t_id}: {e}", flush=True)

    # 7. Sync Feeder Supply Relationships (33kV -> 11kV)
    print("\nSyncing Feeder Supply Relationships...", flush=True)
    cursor.execute("SELECT supplier_feeder_id, supplied_feeder_id, supply_type, supply_capacity, priority_order, status, operational_notes FROM feeder_supply_relationships")
    FeederSupplyRelationship.objects.all().delete()
    for s_id, d_id, s_type, cap, priority, status, notes in cursor.fetchall():
        try:
            supplier = Feeder.objects.filter(slug=s_id).first()
            supplied = Feeder.objects.filter(slug=d_id).first()
            if supplier and supplied:
                FeederSupplyRelationship.objects.create(
                    supplier_feeder=supplier,
                    supplied_feeder=supplied,
                    supply_type=s_type,
                    supply_capacity_mw=cap,
                    priority_order=priority,
                    status=status,
                    operational_notes=notes or ''
                )
        except Exception as e:
            print(f"   ! Error relating {s_id} to {d_id}: {e}", flush=True)

    # 8. Deactivation
    print("\nCleaning up/Deactivating Raven-only records...", flush=True)
    cursor.execute("SELECT injection_station_id FROM injection_stations")
    dn_is_ids = {row[0] for row in cursor.fetchall()}
    # We only deactivate if it looks like a DataNest ID (contains hyphen) 
    # to avoid deactivating manual/test records like 'Test District'
    deactivated_is = InjectionSubstation.objects.filter(status='active', slug__contains='-').exclude(slug__in=dn_is_ids).update(status='inactive')
    print(f"   - Deactivated {deactivated_is} substations not in DataNest", flush=True)

    cursor.execute("SELECT feeder_id FROM feeders")
    dn_feeder_ids = {row[0] for row in cursor.fetchall()}
    deactivated_feeders = Feeder.objects.filter(status='active', slug__contains='-').exclude(slug__in=dn_feeder_ids).update(status='inactive')
    print(f"   - Deactivated {deactivated_feeders} feeders not in DataNest", flush=True)

    print("\nSync Complete!", flush=True)



if __name__ == "__main__":
    sync_mappings()
