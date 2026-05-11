from config import constants as adv_consts
from config import game_settings as adv_config
from worlds.models import Room


ROOM_COSTS = {
    adv_consts.ROOM_TYPE_ROAD: 1,
    adv_consts.ROOM_TYPE_CITY: 1,
    adv_consts.ROOM_TYPE_INDOOR: 1,
    adv_consts.ROOM_TYPE_FIELD: 2,
    adv_consts.ROOM_TYPE_TRAIL: 2,
    adv_consts.ROOM_TYPE_MOUNTAIN: 4,
    adv_consts.ROOM_TYPE_FOREST: 3,
    adv_consts.ROOM_TYPE_DESERT: 3,
    adv_consts.ROOM_TYPE_WATER: 3,
    adv_consts.ROOM_TYPE_SHALLOW: 3,
}


def movement_cost(room: Room) -> int:
    return ROOM_COSTS.get(room.type, adv_config.MOVEMENT_COST)
