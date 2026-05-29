# grid_view/views.py
from math import ceil
from datetime import date, datetime

from django.db.models import Count, Sum
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.models import MonthlyTechnicalSummary
from commercial.models import CommercialCustomer, MeterReading
from common.models import Feeder
from financial.models import Opex, SalaryPayment
from users.models import UserSectionAccess


# ── Helpers ────────────────────────────────────────────────────────────────────

ALL_SECTIONS = {'overview', 'commercial', 'financial', 'technical', 'hr', 'regulatory'}


def _get_user_sections(user):
    """Return set of section names the user has active access to."""
    if user.role == 'super_admin':
        return ALL_SECTIONS
    return set(
        UserSectionAccess.objects.filter(user=user, is_active=True)
        .values_list('section__name', flat=True)
    )


def _parse_month(month_str):
    """Parse '2026-01' → date(2026, 1, 1). Defaults to current month."""
    if month_str:
        try:
            parsed = datetime.strptime(month_str, '%Y-%m')
            return date(parsed.year, parsed.month, 1)
        except ValueError:
            pass
    today = date.today()
    return date(today.year, today.month, 1)


def _parse_uuid(value):
    """Return value unchanged if it looks like a valid UUID, else None."""
    if not value:
        return None
    try:
        from uuid import UUID as _UUID
        _UUID(str(value))
        return value
    except (ValueError, AttributeError):
        return None


def _build_columns(sections):
    """Return ordered column definitions based on accessible sections."""
    columns = [
        {'key': 'state',         'label': 'State',             'group': 'infrastructure'},
        {'key': 'district',      'label': 'Business District',  'group': 'infrastructure'},
        {'key': 'feeder',        'label': 'Feeder',             'group': 'infrastructure'},
        {'key': 'band',          'label': 'Band',               'group': 'infrastructure'},
        {'key': 'voltage_level', 'label': 'Voltage Level',      'group': 'infrastructure'},
        {'key': 'feeder_status', 'label': 'Status',             'group': 'infrastructure'},
        {'key': 'is_onboarded',  'label': 'Onboarded',          'group': 'infrastructure'},
    ]

    if 'technical' in sections:
        columns += [
            {'key': 'energy_delivered_mwh',      'label': 'Energy Delivered (MWh)',        'group': 'technical'},
            {'key': 'avg_hours_of_supply',        'label': 'Avg Hours of Supply',           'group': 'technical'},
            {'key': 'avg_peak_load_mw',           'label': 'Avg Peak Load (MW)',            'group': 'technical'},
            {'key': 'total_interruptions',        'label': 'Total Interruptions',           'group': 'technical'},
            {'key': 'avg_interruption_duration',  'label': 'Avg Interruption Duration (h)', 'group': 'technical'},
            {'key': 'load_shedding_hours',        'label': 'Load Shedding (h)',             'group': 'technical'},
            {'key': 'avg_turnaround_time',        'label': 'Avg Turnaround Time (h)',       'group': 'technical'},
            {'key': 'saifi',                      'label': 'SAIFI',                         'group': 'technical'},
            {'key': 'saidi',                      'label': 'SAIDI',                         'group': 'technical'},
        ]

    if 'commercial' in sections:
        columns += [
            {'key': 'total_customers',       'label': 'Total Customers',         'group': 'commercial'},
            {'key': 'meter_readings_count',  'label': 'Meter Readings',          'group': 'commercial'},
            {'key': 'total_consumption_kwh', 'label': 'Total Consumption (kWh)', 'group': 'commercial'},
        ]

    if 'financial' in sections:
        columns += [
            {'key': 'total_opex',     'label': 'Total OPEX (₦)',    'group': 'financial'},
            {'key': 'total_salaries', 'label': 'Total Salaries (₦)', 'group': 'financial'},
        ]

    return columns


# ── Data fetchers (each returns a dict keyed by id) ───────────────────────────

def _fetch_technical(month_date, feeder_ids):
    """feeder_id (str) → technical metrics dict."""
    qs = MonthlyTechnicalSummary.objects.filter(
        month=month_date,
        feeder_id__in=feeder_ids,
        feeder__isnull=False,
        state__isnull=True,
        business_district__isnull=True,
    ).only(
        'feeder_id', 'total_energy_delivered', 'avg_hours_of_supply',
        'avg_peak_load', 'total_interruptions', 'avg_interruption_duration',
        'load_shedding_hours', 'avg_turnaround_time', 'saifi', 'saidi',
    )
    result = {}
    for row in qs:
        result[str(row.feeder_id)] = {
            'energy_delivered_mwh':     float(row.total_energy_delivered),
            'avg_hours_of_supply':      float(row.avg_hours_of_supply),
            'avg_peak_load_mw':         float(row.avg_peak_load),
            'total_interruptions':      row.total_interruptions,
            'avg_interruption_duration': float(row.avg_interruption_duration),
            'load_shedding_hours':      float(row.load_shedding_hours),
            'avg_turnaround_time':      float(row.avg_turnaround_time),
            'saifi':                    float(row.saifi),
            'saidi':                    float(row.saidi),
        }
    return result


def _fetch_commercial(month_date, feeder_ids):
    """feeder_id (str) → commercial metrics dict. Only onboarded feeders count."""
    # Customer counts per feeder — onboarded feeders only
    cust_map = {
        str(r['feeder']): r['cnt']
        for r in CommercialCustomer.objects
            .filter(feeder_id__in=feeder_ids, feeder__commercial_is_onboarded=True)
            .values('feeder')
            .annotate(cnt=Count('id'))
    }

    # Meter readings aggregated per feeder for the given month — onboarded feeders only
    reading_map = {}
    for r in (
        MeterReading.objects
        .filter(
            reading_date__year=month_date.year,
            reading_date__month=month_date.month,
            customer__feeder_id__in=feeder_ids,
            customer__feeder__commercial_is_onboarded=True,
        )
        .values('customer__feeder')
        .annotate(cnt=Count('id'), consumption=Sum('billed_consumption'))
    ):
        reading_map[str(r['customer__feeder'])] = {
            'meter_readings_count':  r['cnt'],
            'total_consumption_kwh': float(r['consumption'] or 0),
        }

    result = {}
    for fid in {str(f) for f in feeder_ids}:
        result[fid] = {
            'total_customers':       cust_map.get(fid, 0),
            'meter_readings_count':  reading_map.get(fid, {}).get('meter_readings_count', 0),
            'total_consumption_kwh': reading_map.get(fid, {}).get('total_consumption_kwh', 0.0),
        }
    return result


def _fetch_financial(month_date, district_ids):
    """district_id (str) → financial metrics dict."""
    from django.db.models import Sum

    opex_map = {
        str(r['district']): float(r['total'] or 0)
        for r in Opex.objects
            .filter(date__year=month_date.year, date__month=month_date.month, district_id__in=district_ids)
            .values('district')
            .annotate(total=Sum('credit'))
    }
    salary_map = {
        str(r['district']): float(r['total'] or 0)
        for r in SalaryPayment.objects
            .filter(month__year=month_date.year, month__month=month_date.month, district_id__in=district_ids)
            .values('district')
            .annotate(total=Sum('amount'))
    }
    result = {}
    for did in {str(d) for d in district_ids}:
        result[did] = {
            'total_opex':     opex_map.get(did, 0.0),
            'total_salaries': salary_map.get(did, 0.0),
        }
    return result


# ── Aggregation helpers ────────────────────────────────────────────────────────

def _agg_tech(rows):
    if not rows:
        return {k: None for k in (
            'energy_delivered_mwh', 'avg_hours_of_supply', 'avg_peak_load_mw',
            'total_interruptions', 'avg_interruption_duration',
            'load_shedding_hours', 'avg_turnaround_time', 'saifi', 'saidi',
        )}
    n = len(rows)
    return {
        'energy_delivered_mwh':     round(sum(r['energy_delivered_mwh'] for r in rows), 4),
        'avg_hours_of_supply':      round(sum(r['avg_hours_of_supply'] for r in rows) / n, 2),
        'avg_peak_load_mw':         round(sum(r['avg_peak_load_mw'] for r in rows) / n, 2),
        'total_interruptions':      sum(r['total_interruptions'] for r in rows),
        'avg_interruption_duration': round(sum(r['avg_interruption_duration'] for r in rows) / n, 2),
        'load_shedding_hours':      round(sum(r['load_shedding_hours'] for r in rows), 2),
        'avg_turnaround_time':      round(sum(r['avg_turnaround_time'] for r in rows) / n, 2),
        'saifi':                    round(sum(r['saifi'] for r in rows) / n, 4),
        'saidi':                    round(sum(r['saidi'] for r in rows) / n, 4),
    }


def _agg_commercial(rows):
    if not rows:
        return {'total_customers': None, 'meter_readings_count': None, 'total_consumption_kwh': None}
    return {
        'total_customers':       sum(r['total_customers'] for r in rows),
        'meter_readings_count':  sum(r['meter_readings_count'] for r in rows),
        'total_consumption_kwh': round(sum(r['total_consumption_kwh'] for r in rows), 2),
    }


def _agg_financial(rows):
    if not rows:
        return {'total_opex': None, 'total_salaries': None}
    return {
        'total_opex':     round(sum(r['total_opex'] for r in rows), 2),
        'total_salaries': round(sum(r['total_salaries'] for r in rows), 2),
    }


# ── Main view ──────────────────────────────────────────────────────────────────

class GridViewAPIView(APIView):
    """
    GET /api/grid-view/

    Returns all infrastructure data in flat tabular format.
    Columns are dynamic based on the requesting user's section access.

    Query params:
      group_by   feeder (default) | district | state
      month      YYYY-MM  (defaults to current month)
      state_id   filter by state UUID
      district_id filter by district UUID
      page       page number (default 1)
      page_size  rows per page (default 50, max 200)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # ── Access check ──────────────────────────────────────────────────────
        user_sections = _get_user_sections(request.user)
        if not user_sections:
            return Response(
                {'detail': 'No section access granted. Contact your administrator.'},
                status=403,
            )

        # ── Parse params ──────────────────────────────────────────────────────
        group_by = request.query_params.get('group_by', 'feeder')
        if group_by not in ('feeder', 'district', 'state'):
            group_by = 'feeder'

        month_date  = _parse_month(request.query_params.get('month'))
        state_id    = _parse_uuid(request.query_params.get('state_id'))
        district_id = _parse_uuid(request.query_params.get('district_id'))

        try:
            page      = max(1, int(request.query_params.get('page', 1)))
            page_size = min(max(1, int(request.query_params.get('page_size', 50))), 200)
        except (ValueError, TypeError):
            page, page_size = 1, 50

        # ── Section flags ─────────────────────────────────────────────────────
        inc_tech       = 'technical'  in user_sections
        inc_commercial = 'commercial' in user_sections
        inc_financial  = 'financial'  in user_sections

        sections_included = (
            (['technical']  if inc_tech       else []) +
            (['commercial'] if inc_commercial else []) +
            (['financial']  if inc_financial  else [])
        )

        # ── Columns ───────────────────────────────────────────────────────────
        columns = _build_columns(user_sections)

        # ── Base feeder queryset ──────────────────────────────────────────────
        feeder_qs = Feeder.objects.select_related(
            'band',
            'business_district',
            'business_district__state',
        )
        if state_id:
            feeder_qs = feeder_qs.filter(business_district__state_id=state_id)
        if district_id:
            feeder_qs = feeder_qs.filter(business_district_id=district_id)

        feeders = list(
            feeder_qs.order_by(
                'business_district__state__name',
                'business_district__name',
                'name',
            )
        )

        if not feeders:
            return Response({
                'group_by':          group_by,
                'period':            month_date.strftime('%Y-%m'),
                'sections_included': sections_included,
                'columns':           columns,
                'rows':              [],
                'total_rows':        0,
                'page':              page,
                'page_size':         page_size,
                'total_pages':       0,
            })

        feeder_ids   = [f.id for f in feeders]
        district_ids = list({f.business_district_id for f in feeders if f.business_district_id})

        # ── Fetch metric maps (bulk, no N+1) ──────────────────────────────────
        tech_map  = _fetch_technical(month_date, feeder_ids)   if inc_tech       else {}
        comm_map  = _fetch_commercial(month_date, feeder_ids)  if inc_commercial else {}
        fin_map   = _fetch_financial(month_date, district_ids) if inc_financial  else {}

        # ── Build rows ────────────────────────────────────────────────────────
        rows = []

        if group_by == 'feeder':
            for f in feeders:
                fid = str(f.id)
                did = str(f.business_district_id) if f.business_district_id else None

                row = {
                    'state':         f.business_district.state.name if f.business_district and f.business_district.state else None,
                    'district':      f.business_district.name if f.business_district else None,
                    'feeder':        f.name,
                    'band':          f.band.name if f.band else None,
                    'voltage_level': f.get_voltage_level_display(),
                    'feeder_status': f.status,
                    'is_onboarded':  f.is_onboarded,
                }
                if inc_tech:
                    row.update(tech_map.get(fid, {
                        'energy_delivered_mwh': None, 'avg_hours_of_supply': None,
                        'avg_peak_load_mw': None, 'total_interruptions': None,
                        'avg_interruption_duration': None, 'load_shedding_hours': None,
                        'avg_turnaround_time': None, 'saifi': None, 'saidi': None,
                    }))
                if inc_commercial:
                    row.update(comm_map.get(fid, {
                        'total_customers': None, 'meter_readings_count': None,
                        'total_consumption_kwh': None,
                    }))
                if inc_financial and did:
                    row.update(fin_map.get(did, {'total_opex': None, 'total_salaries': None}))
                elif inc_financial:
                    row.update({'total_opex': None, 'total_salaries': None})

                rows.append(row)

        elif group_by == 'district':
            # Group feeders by district
            district_buckets: dict = {}
            for f in feeders:
                key = str(f.business_district_id) if f.business_district_id else '__none__'
                district_buckets.setdefault(key, []).append(f)

            for did, bucket in district_buckets.items():
                first  = bucket[0]
                b_fids = [str(f.id) for f in bucket]

                row = {
                    'state':         first.business_district.state.name if first.business_district and first.business_district.state else None,
                    'district':      first.business_district.name if first.business_district else None,
                    'feeder':        None,
                    'band':          None,
                    'voltage_level': None,
                    'feeder_status': None,
                    'is_onboarded':  None,
                    'feeder_count':  len(bucket),
                }
                if inc_tech:
                    row.update(_agg_tech([tech_map[fid] for fid in b_fids if fid in tech_map]))
                if inc_commercial:
                    row.update(_agg_commercial([comm_map[fid] for fid in b_fids if fid in comm_map]))
                if inc_financial:
                    row.update(fin_map.get(did, {'total_opex': None, 'total_salaries': None}))

                rows.append(row)

        elif group_by == 'state':
            # Group feeders by state
            state_buckets: dict = {}
            for f in feeders:
                key = str(f.business_district.state_id) if f.business_district and f.business_district.state_id else '__none__'
                state_buckets.setdefault(key, []).append(f)

            for sid, bucket in state_buckets.items():
                first  = bucket[0]
                s_fids = [str(f.id) for f in bucket]
                s_dids = list({str(f.business_district_id) for f in bucket if f.business_district_id})

                row = {
                    'state':          first.business_district.state.name if first.business_district and first.business_district.state else None,
                    'district':       None,
                    'feeder':         None,
                    'band':           None,
                    'voltage_level':  None,
                    'feeder_status':  None,
                    'is_onboarded':   None,
                    'feeder_count':   len(bucket),
                    'district_count': len(s_dids),
                }
                if inc_tech:
                    row.update(_agg_tech([tech_map[fid] for fid in s_fids if fid in tech_map]))
                if inc_commercial:
                    row.update(_agg_commercial([comm_map[fid] for fid in s_fids if fid in comm_map]))
                if inc_financial:
                    row.update(_agg_financial([fin_map[did] for did in s_dids if did in fin_map]))

                rows.append(row)

        # ── Paginate ──────────────────────────────────────────────────────────
        total      = len(rows)
        start      = (page - 1) * page_size
        paged_rows = rows[start:start + page_size]

        return Response({
            'group_by':          group_by,
            'period':            month_date.strftime('%Y-%m'),
            'sections_included': sections_included,
            'columns':           columns,
            'rows':              paged_rows,
            'total_rows':        total,
            'page':              page,
            'page_size':         page_size,
            'total_pages':       ceil(total / page_size) if total else 1,
        })
