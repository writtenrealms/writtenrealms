from system.models import SiteControl


def building_enabled(site_name='prod'):
    """Whether any signed-up user may build. Defaults to open without a row."""
    enabled = SiteControl.objects.filter(name=site_name).values_list(
        'building_enabled', flat=True).first()
    return True if enabled is None else enabled


def can_create_worlds(user):
    if not user.is_authenticated or user.is_temporary:
        return False
    return user.is_staff or building_enabled()
