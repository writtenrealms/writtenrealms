from django.contrib import admin

from system import models as system_models
from core.admin import BaseAdmin


class IntroConfigAdmin(BaseAdmin):
    list_display = ('id', 'world')
    raw_id_fields = ['world']


class SiteControlAdmin(BaseAdmin):
    list_display = ('name', 'maintenance_mode')


class EdeusUniquesAdmin(BaseAdmin):
    list_display = ('id', 'run_ts', 'assassin', 'cleric', 'mage', 'warrior')
    raw_id_fields = ['assassin', 'cleric', 'mage', 'warrior']


class NexusAdmin(BaseAdmin):
    list_display = ('id', 'name', 'state')


class IPBanAdmin(BaseAdmin):
    list_display = ('id', 'ip', 'reason')


admin.site.register(system_models.EdeusUniques, EdeusUniquesAdmin)
admin.site.register(system_models.IntroConfig, IntroConfigAdmin)
admin.site.register(system_models.SiteControl, SiteControlAdmin)
admin.site.register(system_models.Nexus, NexusAdmin)
admin.site.register(system_models.IPBan, IPBanAdmin)