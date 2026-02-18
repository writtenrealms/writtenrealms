from typing import Literal

from pydantic import BaseModel

from system.models import SiteControl


class PlatformPolicy(BaseModel):
    world_creation: Literal['all', 'none', 'whitelist'] = 'all'


def get_platform_policy(site_name='prod'):
    site_control = SiteControl.objects.filter(name=site_name).first()
    policy_data = {}
    if site_control and isinstance(site_control.platform_policy, dict):
        policy_data = site_control.platform_policy
    return PlatformPolicy.model_validate(policy_data)
