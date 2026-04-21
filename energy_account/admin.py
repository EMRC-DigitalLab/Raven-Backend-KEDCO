from django.contrib import admin

from .models import (
    EACouplingLog,
    EAGridMeter,
    EAMeterCheckRecord,
    EAMeterCheckSchedule,
    EAMOReconciliation,
    EAMonthlyReading,
    EAMonthlyReturn,
    EAFeederTechnicalEnergy,
    EASettings,
    EAStationAssignment,
    EATCNReconciliation,
    EATCNReconciliationNote,
    EAWeeklyReading,
    NBETMarketBilateral,
)

admin.site.register(NBETMarketBilateral)
admin.site.register(EAGridMeter)
admin.site.register(EAMonthlyReturn)
admin.site.register(EAMonthlyReading)
admin.site.register(EAFeederTechnicalEnergy)
admin.site.register(EATCNReconciliation)
admin.site.register(EATCNReconciliationNote)
admin.site.register(EAMOReconciliation)
admin.site.register(EAWeeklyReading)
admin.site.register(EAMeterCheckSchedule)
admin.site.register(EAMeterCheckRecord)
admin.site.register(EAStationAssignment)
admin.site.register(EACouplingLog)
admin.site.register(EASettings)
