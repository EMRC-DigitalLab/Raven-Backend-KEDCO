from commercial.utils import get_filtered_feeders
from common.models import Feeder


class FeederFilteredQuerySetMixin:
    feeder_lookup_field = 'feeder'

    def filter_by_location(self, queryset):
        feeders = get_filtered_feeders(self.request)
        return queryset.filter(**{f"{self.feeder_lookup_field}__in": feeders})
