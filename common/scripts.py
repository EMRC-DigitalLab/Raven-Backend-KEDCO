from django.utils.text import slugify

from .models import BusinessDistrict, Feeder, InjectionSubstation, State


def update_slugs():
    # Update States
    for state in State.objects.all():
        new_slug = slugify(state.name)
        if state.slug != new_slug:
            state.slug = new_slug
            state.save(update_fields=["slug"])
    print("✅ State slugs updated.")

    # Update Business Districts
    for district in BusinessDistrict.objects.all():
        new_slug = slugify(district.name)
        if district.slug != new_slug:
            district.slug = new_slug
            district.save(update_fields=["slug"])
    print("✅ BusinessDistrict slugs updated.")

    # Update Injection Substations
    for substation in InjectionSubstation.objects.all():
        new_slug = slugify(substation.name)
        if substation.slug != new_slug:
            substation.slug = new_slug
            substation.save(update_fields=["slug"])
    print("✅ InjectionSubstation slugs updated.")

    # Update Feeders
    for feeder in Feeder.objects.all():
        new_slug = slugify(feeder.name)
        if feeder.slug != new_slug:
            feeder.slug = new_slug
            feeder.save(update_fields=["slug"])
    print("✅ Feeder slugs updated.")
