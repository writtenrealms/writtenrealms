from django import forms
from django.contrib import admin

from worlds.models import World

class BaseAdmin(admin.ModelAdmin):
    """
    Base admin capable of displaying specified text fields as char fields
    """

    ordering = ['-id']

    def formfield_for_dbfield(self, db_field, **kwargs):
        formfield = super(BaseAdmin, self).formfield_for_dbfield(
            db_field, **kwargs)

        display_as_charfield = getattr(self, 'display_as_charfield', [])
        display_as_choicefield = getattr(self, 'display_as_choicefield', [])

        if db_field.name in display_as_charfield:
            formfield.widget = forms.TextInput(attrs=formfield.widget.attrs)
        elif db_field.name in display_as_choicefield:
            formfield.widget = forms.Select(choices=formfield.choices,
                                            attrs=formfield.widget.attrs)

        return formfield


class ContextRootWorldFilter(admin.SimpleListFilter):
    title = 'World'
    parameter_name = 'world'
    def lookups(self, request, model_admin):
        return World.objects.filter(context__isnull=True).values_list(
            'id', 'name')
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(world__context_id=self.value())
        return queryset


class DirectRootWorldFilter(admin.SimpleListFilter):
    """
    Filter by world for entites where 'world' refers to a root world, for
    example templates or quests.
    """
    title = 'By world'
    parameter_name = 'world'
    def lookups(self, request, model_admin):
        return World.objects.filter(context__isnull=True).values_list(
            'id', 'name')
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(world_id=self.value())
        return queryset