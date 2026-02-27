import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

from common.models import (
    State, BusinessDistrict, InjectionSubstation, Feeder, 
    DistributionTransformer, PowerTransformer, 
    FeederTransformerMapping, FeederSupplyRelationship
)

def verify():
    print("=== Model Verification ===")
    print(f"States count: {State.objects.count()}")
    print(f"Districts count: {BusinessDistrict.objects.count()}")
    print(f"Substations count: {InjectionSubstation.objects.count()}")
    print(f"Feeders count: {Feeder.objects.count()}")
    print(f"Distribution Transformers count: {DistributionTransformer.objects.count()}")
    
    print("\n=== New Infrastructure Models ===")
    print(f"Power Transformers: {PowerTransformer.objects.count()}")
    print(f"Feeder Transformer Mappings: {FeederTransformerMapping.objects.count()}")
    print(f"Feeder Supply Relationships: {FeederSupplyRelationship.objects.count()}")
    
    # Check new fields
    sub = InjectionSubstation.objects.first()
    if sub:
        print(f"\nSubstation '{sub.name}' check:")
        print(f"  Station Type: {sub.station_type}")
        print(f"  Status: {sub.status}")
        print(f"  State: {sub.state}")
        
    feeder = Feeder.objects.first()
    if feeder:
        print(f"\nFeeder '{feeder.name}' check:")
        print(f"  Feeder Class: '{feeder.feeder_class}'")
        print(f"  Status: {feeder.status}")

if __name__ == "__main__":
    verify()
