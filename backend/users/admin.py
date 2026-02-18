from django.contrib import admin

from users.models import User, UserFlag


class UserAdmin(admin.ModelAdmin):

    list_display = [
        'id',
        'email',
        'username',
        'date_joined',
        'send_newsletter',
        'is_confirmed'
    ]
    list_filter = [
        'is_temporary',
        'is_invalid',
        'send_newsletter',
        'is_confirmed'
    ]
    search_fields = ['email', 'username']


class UserFlagAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'code',
    ]
    list_filter = ['code']
    raw_id_fields = ['user']
    search_fields = ['user__email', 'user__username', 'user__id']


admin.site.register(User, UserAdmin)
admin.site.register(UserFlag, UserFlagAdmin)