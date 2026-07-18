from config import constants


def type_to_slot(eq_type, has_weapon=False, has_offhand=False, archetype=None):
    "For a given equipment type, return the slot it should be equipped in"
    if eq_type == constants.EQUIPMENT_TYPE_WEAPON_2H:
        return constants.EQUIPMENT_SLOT_WEAPON
    elif eq_type == constants.EQUIPMENT_TYPE_WEAPON_1H:
        if not has_weapon:
            return constants.EQUIPMENT_SLOT_WEAPON
        elif not has_offhand and archetype == constants.ARCHETYPE_ASSASSIN:
            return constants.EQUIPMENT_SLOT_OFFHAND
    elif eq_type == constants.EQUIPMENT_TYPE_SHIELD:
        return constants.EQUIPMENT_SLOT_OFFHAND
    elif eq_type in constants.EQUIPMENT_SLOTS:
        return eq_type
    return None


def calculate_power(item, archetype):
    """
    Given a certain archetype using an item, return an estimate for
    how strong that item is compared to others.

    World-authored attributes are intentionally generic here. Canonical
    combat stats still use the existing budget weights.
    """
    from config import constants as adv_consts

    total_value = 0
    for stat_value in (item.attributes or {}).values():
        if stat_value:
            total_value += stat_value * 10
    for stat, weight in adv_consts.ATTR_BUDGET.items():
        stat_value = getattr(item, stat, 0)
        if stat_value:
            stat_value = stat_value * weight
            total_value += stat_value
    return total_value
