from django.db.models import Count, Q


def list_locations(entity: str = 'feeder', district: str = None, state: str = None, onboarded_only: bool = True) -> dict:
    """
    List available entities in Raven.
    entity: 'feeder' | 'district' | 'state' | 'substation'
    """
    from common.models import BusinessDistrict, Feeder, InjectionSubstation, State

    if entity == 'state':
        rows = State.objects.all().values('name', 'slug').order_by('name')
        return {'entity': 'state', 'results': list(rows), 'count': rows.count()}

    if entity == 'district':
        qs = BusinessDistrict.objects.select_related('state')
        if state:
            obj = State.objects.filter(Q(slug=state) | Q(name__icontains=state)).first()
            if obj:
                qs = qs.filter(state=obj)
        rows = qs.values('name', 'slug', 'state__name').order_by('state__name', 'name')
        return {
            'entity': 'district',
            'results': [{'name': r['name'], 'slug': r['slug'], 'state': r['state__name']} for r in rows],
            'count': rows.count(),
        }

    if entity == 'substation':
        qs = InjectionSubstation.objects.filter(status='active').select_related('state')
        if state:
            obj = State.objects.filter(Q(slug=state) | Q(name__icontains=state)).first()
            if obj:
                qs = qs.filter(state=obj)
        rows = qs.values('name', 'slug', 'state__name', 'station_type').order_by('name')
        return {
            'entity': 'substation',
            'results': [{'name': r['name'], 'slug': r['slug'], 'state': r['state__name'], 'type': r['station_type']} for r in rows],
            'count': rows.count(),
        }

    # feeder (default)
    qs = Feeder.objects.select_related('business_district', 'business_district__state', 'substation')
    if onboarded_only:
        qs = qs.filter(is_onboarded=True)
    if district:
        obj = BusinessDistrict.objects.filter(Q(slug=district) | Q(name__icontains=district)).first()
        if obj:
            qs = qs.filter(business_district=obj)
    if state:
        obj = State.objects.filter(Q(slug=state) | Q(name__icontains=state)).first()
        if obj:
            qs = qs.filter(business_district__state=obj)

    rows = qs.values(
        'name', 'slug', 'voltage_level', 'feeder_class',
        'business_district__name', 'business_district__state__name', 'substation__name',
    ).order_by('business_district__name', 'name')

    return {
        'entity': 'feeder',
        'onboarded_only': onboarded_only,
        'results': [
            {
                'name': r['name'],
                'slug': r['slug'],
                'voltage': r['voltage_level'],
                'class': r['feeder_class'],
                'district': r['business_district__name'],
                'state': r['business_district__state__name'],
                'substation': r['substation__name'],
            }
            for r in rows
        ],
        'count': rows.count(),
    }


def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web for recent energy sector news, NERC orders, or any relevant information."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return {
            'query': query,
            'results': [
                {
                    'title': r.get('title', ''),
                    'snippet': r.get('body', ''),
                    'url': r.get('href', ''),
                }
                for r in results
            ],
            'count': len(results),
        }
    except ImportError:
        return {'query': query, 'error': 'Web search unavailable — duckduckgo-search package not installed.'}
    except Exception as e:
        return {'query': query, 'error': str(e), 'results': []}
