from django.contrib import admin

from lobby.models import FeaturedWorld, DiscoverWorld, InDevelopmentWorld

# Register your models here.
class FeaturedWorldAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'world')
    raw_id_fields = ['world']
admin.site.register(FeaturedWorld, FeaturedWorldAdmin)


class DiscoverWorldAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'world')
    raw_id_fields = ['world']
admin.site.register(DiscoverWorld, DiscoverWorldAdmin)


class InDevelopmentWorldAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'world')
    raw_id_fields = ['world']
admin.site.register(InDevelopmentWorld, InDevelopmentWorldAdmin)