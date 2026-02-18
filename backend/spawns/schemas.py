"""
Pydantic schemas for WR2 state.sync payload.

These schemas define the shape of data sent to the frontend when a player
enters a world. The goal is to match the shape that WR1's connect_data
returned.

Top-level structure:
- map: List of rooms the player has visited (for minimap)
- actor: Full player data
- room: Current room with characters, inventory, actions, etc.
- world: World configuration (skills, feats, factions, currencies)
- who_list: List of connected players

Reference: legacy WR1 resource payloads.
"""

from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


# =============================================================================
# Equipment & Items
# =============================================================================

class Item(BaseModel):
    """Item data - used for inventory items and room inventory."""
    key: str
    name: str
    cf_name: str = ""  # Capitalized first name
    type: Optional[str] = None  # "equippable", "container", "food", etc.
    armor_class: Optional[str] = None  # "light", "heavy"
    description: Optional[str] = None
    ground_description: Optional[str] = None
    level: int = 1
    quality: str = "normal"  # "normal", "imbued", "enchanted", "epic", "legendary"
    is_magic: bool = False
    equipment_type: Optional[str] = None  # "weapon_1h", "weapon_2h", "head", etc.
    template: Optional[str] = None
    template_id: Optional[int] = None

    # Stats
    strength: int = 0
    agility: int = 0
    constitution: int = 0
    dexterity: int = 0
    intelligence: int = 0
    max_stamina: int = 0

    # Combat stats
    attack_power: int = 0
    spell_power: int = 0
    armor: int = 0
    crit: int = 0
    resilience: int = 0
    dodge: int = 0

    health_max: int = 0
    health_regen: int = 0
    mana_max: int = 0
    mana_regen: int = 0
    stamina_max: int = 0
    stamina_regen: int = 0

    # Properties
    is_container: bool = False
    is_pickable: bool = True
    cost: int = 0
    currency: str = "gold"

    # Identifiers
    keywords: str = ""
    keyword: Optional[str] = None  # First keyword
    label: Optional[str] = None

    # Upgrades
    upgrade_cost: int = 0
    upgrade_count: int = 0

    # Weapon-specific
    weapon_type: Optional[str] = None

    # Container inventory (for corpses, bags, etc.)
    inventory: List["Item"] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)


class Equipment(BaseModel):
    """Character equipment slots."""
    weapon: Optional[Item] = None
    offhand: Optional[Item] = None
    head: Optional[Item] = None
    body: Optional[Item] = None
    arms: Optional[Item] = None
    hands: Optional[Item] = None
    waist: Optional[Item] = None
    legs: Optional[Item] = None
    feet: Optional[Item] = None
    accessory: Optional[Item] = None


# =============================================================================
# Characters (Players & Mobs)
# =============================================================================

class Target(BaseModel):
    """Minimal target info for combat."""
    id: int
    key: str
    name: str
    health: int = 0
    health_max: int = 0
    level: int = 1
    keywords: str = ""


class QuestData(BaseModel):
    """Quest availability info for NPCs."""
    enquire: bool = False
    complete: bool = False


class Char(BaseModel):
    """
    Character data - used for chars in room (both players and mobs).
    This is the 'room' representation from CharResource.
    """
    id: int
    key: str
    name: str
    template_id: Optional[int] = None
    title: Optional[str] = ""
    description: Optional[str] = None
    archetype: Optional[str] = None
    core_faction: Optional[str] = None
    room_description: Optional[str] = None

    state: str = "standing"  # "standing", "combat", "sitting", "sleeping"
    stance: str = "normal"  # "normal", "aggressive", "defensive", etc.

    health: int = 0
    health_max: int = 0
    mana: int = 0
    level: int = 1
    gender: str = "male"
    pronouns: Optional[str] = None

    target: Optional[Target] = None
    group_id: Optional[str] = None

    equipment: Optional[Equipment] = None

    keywords: str = ""
    keyword: Optional[str] = None  # First keyword

    actions: List[str] = Field(default_factory=list)
    display_faction: Optional[str] = None

    is_invisible: bool = False
    is_linkless: bool = False
    is_elite: bool = False

    char_type: Literal["player", "mob"] = "mob"

    quest_data: QuestData = Field(default_factory=QuestData)
    is_upgrader: bool = False
    upgrade_cost_multiplier: float = 0.0


class PlayerSkills(BaseModel):
    """Player skill selections."""
    custom: Dict[str, str] = Field(default_factory=dict)


class Alias(BaseModel):
    """Player alias."""
    id: int
    match: str
    replacement: str


class Actor(BaseModel):
    """
    Full player data sent as 'actor' in state.sync.
    This is the 'full' representation from PlayerResource.
    """
    id: int
    key: str
    name: str
    title: Optional[str] = ""
    level: int = 1
    gender: str = "male"
    keywords: str = ""
    keyword: Optional[str] = None
    description: Optional[str] = None

    archetype: Optional[str] = None
    core_faction: Optional[str] = None
    display_faction: Optional[str] = None
    room: Optional[Dict[str, str]] = None  # Room reference {key: "room.xxx"}
    home: Optional[str] = None  # Home room key

    state: str = "standing"
    stance: str = "normal"

    # Vitals
    health: int = 0
    health_max: int = 0
    health_regen: int = 0
    stamina: int = 0
    stamina_max: int = 0
    stamina_regen: int = 0
    mana: int = 0
    mana_max: int = 0
    mana_regen: int = 0

    # Combat
    target: Optional[Target] = None
    damage: int = 0
    armor: int = 0
    armor_perc: float = 0.0
    focus: Optional[str] = None

    # Stats
    dexterity: int = 0
    constitution: int = 0
    strength: int = 0
    intelligence: int = 0
    attack_power: int = 0
    spell_power: int = 0
    crit: int = 0
    crit_perc: float = 0.0
    dodge: int = 0
    dodge_perc: float = 0.0
    resilience: int = 0
    resilience_perc: float = 0.0

    # Equipment & inventory
    equipment: Equipment = Field(default_factory=Equipment)
    inventory: List[Item] = Field(default_factory=list)

    # Progression
    experience: int = 0
    experience_progress: int = 0
    experience_needed: int = 0
    gold: int = 0
    glory: int = 0
    medals: int = 0
    currencies: Dict[str, int] = Field(default_factory=dict)

    # Grouping
    group_id: Optional[str] = None

    # Factions
    factions: Dict[str, Any] = Field(default_factory=dict)

    # Skills
    skills: PlayerSkills = Field(default_factory=PlayerSkills)
    trophy: Dict[int, int] = Field(default_factory=dict)

    # Player character type
    char_type: Literal["player"] = "player"

    # Player-specific fields
    is_builder: bool = False
    is_immortal: bool = False
    is_temporary: bool = False
    is_invisible: bool = False
    is_idle: bool = False
    is_muted: bool = False
    is_staff: bool = False
    is_confirmed: bool = True
    link_id: Optional[int] = None

    name_recognition: bool = False
    player_housing: bool = False

    nochat: bool = False
    idle_logout: bool = True
    autoflee: int = 0

    room_description: Optional[str] = None
    aliases: Dict[str, Alias] = Field(default_factory=dict)
    marks: Dict[str, Any] = Field(default_factory=dict)
    clan: Optional[Dict[str, Any]] = None

    # Communication
    mute_list: Optional[str] = None
    channels: Optional[str] = None

    # Effects & cooldowns
    effects: Dict[str, Any] = Field(default_factory=dict)
    cooldowns: Dict[str, Any] = Field(default_factory=dict)

    # User-level
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)

    # Language
    language_proficiency: Optional[str] = None


# =============================================================================
# Rooms
# =============================================================================

class Zone(BaseModel):
    """Zone reference."""
    key: str
    name: str


class House(BaseModel):
    """Housing info for a room exit."""
    direction: str
    name: str
    key: str


class RoomAction(BaseModel):
    """Action available in a room."""
    id: int
    key: str
    actions: str
    commands: Optional[str] = None
    conditions: Optional[str] = None
    display_action_in_room: bool = True


class MapRoom(BaseModel):
    """
    Simplified room data for the minimap.
    This is the 'map' representation from RoomResource.
    """
    key: str
    x: int = 0
    y: int = 0
    z: int = 0
    type: str = "road"  # "road", "indoor", "water", "path", etc.
    color: Optional[str] = None

    # Exits
    north: Optional[str] = None
    east: Optional[str] = None
    south: Optional[str] = None
    west: Optional[str] = None
    up: Optional[str] = None
    down: Optional[str] = None

    # Door states
    north_door_state: Optional[str] = None
    east_door_state: Optional[str] = None
    south_door_state: Optional[str] = None
    west_door_state: Optional[str] = None
    up_door_state: Optional[str] = None
    down_door_state: Optional[str] = None


class Room(BaseModel):
    """
    Full room data for the current room.
    This is the 'room' representation from RoomResource.
    """
    key: str
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    color: Optional[str] = None

    # Contents
    inventory: List[Item] = Field(default_factory=list)
    chars: List[Char] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)

    # Coordinates
    x: int = 0
    y: int = 0
    z: int = 0
    type: str = "road"

    # Zone
    zone: Optional[Zone] = None

    # Interactables
    hint: Optional[str] = None
    houses: List[House] = Field(default_factory=list)
    details: List[str] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)

    # Exits
    north: Optional[str] = None
    east: Optional[str] = None
    south: Optional[str] = None
    west: Optional[str] = None
    up: Optional[str] = None
    down: Optional[str] = None

    # Door states
    north_door_state: Optional[str] = None
    east_door_state: Optional[str] = None
    south_door_state: Optional[str] = None
    west_door_state: Optional[str] = None
    up_door_state: Optional[str] = None
    down_door_state: Optional[str] = None


# =============================================================================
# World Configuration
# =============================================================================

class SkillDefinition(BaseModel):
    """Single skill definition."""
    code: str
    name: str
    archetype: Optional[str] = None
    level: int = 1
    default_hotkey: Optional[str] = None
    stances: List[str] = Field(default_factory=list)
    disabled: List[str] = Field(default_factory=list)


class ArchetypeSkills(BaseModel):
    """Skills for a single archetype."""
    # Individual skill definitions keyed by code
    definitions: Dict[str, Any] = Field(default_factory=dict)


class FactionRank(BaseModel):
    """Faction rank definition."""
    standing: int
    name: str
    number: int


class Faction(BaseModel):
    """Faction definition."""
    code: str
    name: str
    death_room: Optional[str] = None
    friendly: List[str] = Field(default_factory=list)
    hostile: List[str] = Field(default_factory=list)
    ranks: List[FactionRank] = Field(default_factory=list)
    is_default: bool = False
    is_core: bool = False


class Currency(BaseModel):
    """Currency definition."""
    code: str
    name: str
    is_default: bool = False


class Social(BaseModel):
    """Social command messages."""
    # [msg_targetless_self, msg_targetless_other, msg_targeted_self, msg_targeted_target, msg_targeted_other]
    messages: List[str] = Field(default_factory=list)


class Socials(BaseModel):
    """All social commands."""
    cmds: Dict[str, List[str]] = Field(default_factory=dict)
    order: List[str] = Field(default_factory=list)


class World(BaseModel):
    """
    World configuration data.
    This is the 'game' representation from WorldResource +
    additional config from AnimateWorldSerializer.
    """
    id: int
    key: str
    name: str
    context: str  # Context world key
    instance_of: Optional[str] = None
    instance_ref: Optional[str] = None

    is_multiplayer: bool = False
    tier: int = 1
    leader: Optional[str] = None

    # Config flags
    never_reload: bool = False
    has_corpse_decay: bool = True
    auto_equip: bool = True
    globals_enabled: bool = False
    classless: bool = False

    # Room references
    starting_room: Optional[str] = None
    death_room: Optional[str] = None

    # Death settings
    death_mode: str = "flee"  # "flee", "respawn", etc.
    death_route: str = ""
    death_gold_penalty: float = 0.0

    # Combat settings
    allow_combat: bool = True
    allow_pvp: bool = False
    flee_to_unknown_rooms: bool = False

    # Player settings
    players_can_set_title: bool = False

    # Game data
    factions: Dict[str, Faction] = Field(default_factory=dict)
    skills: Dict[str, Any] = Field(default_factory=dict)  # Custom skill definitions
    facts: Dict[str, Any] = Field(default_factory=dict)
    socials: Socials = Field(default_factory=Socials)
    currencies: Dict[str, Currency] = Field(default_factory=dict)


# =============================================================================
# Who List
# =============================================================================

class WhoListEntry(BaseModel):
    """Entry in the who list."""
    key: str
    name: str
    title: Optional[str] = ""
    level: int = 1
    gender: str = "male"
    is_immortal: bool = False
    is_invisible: bool = False
    is_idle: bool = False
    is_linkless: bool = False
    display_faction: Optional[str] = None
    clan: Optional[Dict[str, Any]] = None


# =============================================================================
# Top-Level State Sync Payload
# =============================================================================

class StateSyncData(BaseModel):
    """
    The complete state.sync payload sent to the frontend when a player
    enters a world. This matches the structure returned by WR1's
    connect_data payload.
    """
    map: List[MapRoom] = Field(default_factory=list)
    actor: Actor
    room: Room
    world: World
    who_list: List[WhoListEntry] = Field(default_factory=list)


# Enable forward references
Item.model_rebuild()


# =============================================================================
# Mock Data Builder
# =============================================================================

def build_mock_state_sync(
    player_id: int = 1,
    player_name: str = "Adventurer",
    world_id: int = 1,
    world_name: str = "The Realm of Shadows",
) -> StateSyncData:
    """
    Build a complete mock StateSyncData payload for UI development.

    This creates realistic mock data that exercises all major UI components:
    - Player stats, equipment, inventory
    - Room with multiple characters, items, and exits
    - World configuration with skills, factions, currencies
    - Minimap with several connected rooms
    """

    # --- Mock Items ---

    iron_sword = Item(
        key="item.1",
        name="iron sword",
        cf_name="Iron sword",
        type="equippable",
        equipment_type="weapon_1h",
        weapon_type="sword",
        level=5,
        quality="normal",
        is_magic=False,
        description="A sturdy iron sword with a leather-wrapped hilt.",
        keywords="iron sword weapon",
        keyword="sword",
        attack_power=15,
        cost=50,
    )

    leather_helm = Item(
        key="item.2",
        name="leather helm",
        cf_name="Leather helm",
        type="equippable",
        equipment_type="head",
        armor_class="light",
        level=3,
        quality="normal",
        description="A simple leather helm offering basic protection.",
        keywords="leather helm armor",
        keyword="helm",
        armor=5,
        constitution=2,
        cost=25,
    )

    chainmail = Item(
        key="item.3",
        name="chainmail armor",
        cf_name="Chainmail armor",
        type="equippable",
        equipment_type="body",
        armor_class="heavy",
        level=5,
        quality="imbued",
        is_magic=True,
        description="Gleaming chainmail that shimmers with a faint enchantment.",
        keywords="chainmail armor body",
        keyword="chainmail",
        armor=20,
        constitution=5,
        health_max=25,
        cost=200,
    )

    health_potion = Item(
        key="item.4",
        name="health potion",
        cf_name="Health potion",
        type="consumable",
        level=1,
        quality="normal",
        description="A red potion that restores health when consumed.",
        ground_description="A red potion lies here.",
        keywords="health potion red",
        keyword="potion",
        is_pickable=True,
        cost=10,
    )

    gold_ring = Item(
        key="item.5",
        name="gold ring",
        cf_name="Gold ring",
        type="equippable",
        equipment_type="accessory",
        level=8,
        quality="enchanted",
        is_magic=True,
        description="A golden ring set with a small ruby.",
        keywords="gold ring accessory ruby",
        keyword="ring",
        intelligence=3,
        spell_power=10,
        cost=150,
    )

    wooden_shield = Item(
        key="item.6",
        name="wooden shield",
        cf_name="Wooden shield",
        type="equippable",
        equipment_type="shield",
        armor_class="light",
        level=2,
        quality="normal",
        description="A round wooden shield with iron bindings.",
        keywords="wooden shield",
        keyword="shield",
        armor=8,
        cost=30,
    )

    leather_boots = Item(
        key="item.7",
        name="leather boots",
        cf_name="Leather boots",
        type="equippable",
        equipment_type="feet",
        armor_class="light",
        level=3,
        quality="normal",
        description="Comfortable leather boots suitable for travel.",
        keywords="leather boots feet",
        keyword="boots",
        armor=3,
        dexterity=1,
        cost=20,
    )

    bread_loaf = Item(
        key="item.8",
        name="loaf of bread",
        cf_name="Loaf of bread",
        type="food",
        level=1,
        quality="normal",
        description="A fresh loaf of bread.",
        ground_description="A loaf of bread lies here.",
        keywords="loaf bread food",
        keyword="bread",
        is_pickable=True,
        cost=2,
    )

    # --- Mock Equipment ---

    player_equipment = Equipment(
        weapon=iron_sword,
        offhand=wooden_shield,
        head=leather_helm,
        body=chainmail,
        feet=leather_boots,
        accessory=gold_ring,
    )

    # --- Mock Characters in Room ---

    guard_equipment = Equipment(
        weapon=Item(
            key="item.100",
            name="steel halberd",
            cf_name="Steel halberd",
            type="equippable",
            equipment_type="weapon_2h",
            weapon_type="polearm",
            level=10,
            quality="normal",
            keywords="steel halberd weapon",
            keyword="halberd",
            attack_power=25,
        ),
        body=Item(
            key="item.101",
            name="guard's plate armor",
            cf_name="Guard's plate armor",
            type="equippable",
            equipment_type="body",
            armor_class="heavy",
            level=10,
            quality="normal",
            keywords="guard plate armor",
            keyword="armor",
            armor=35,
        ),
    )

    town_guard = Char(
        id=100,
        key="mob.100",
        name="town guard",
        template_id=1,
        title="",
        description="A stern-looking guard in polished armor, vigilantly watching the square.",
        room_description="A town guard stands here, watching the crowd.",
        archetype="warrior",
        core_faction="human",
        state="standing",
        stance="normal",
        health=250,
        health_max=250,
        mana=50,
        level=10,
        gender="male",
        keywords="town guard human male mob.100",
        keyword="guard",
        equipment=guard_equipment,
        display_faction="Town Watch",
        char_type="mob",
        actions=["talk"],
    )

    merchant = Char(
        id=101,
        key="mob.101",
        name="Gregor",
        template_id=2,
        title="the Merchant",
        description="A portly man with a friendly smile, surrounded by various wares.",
        room_description="Gregor the Merchant stands behind his cart, hawking his wares.",
        archetype=None,
        core_faction="human",
        state="standing",
        stance="normal",
        health=80,
        health_max=80,
        mana=20,
        level=5,
        gender="male",
        keywords="gregor merchant human male mob.101",
        keyword="gregor",
        display_faction=None,
        char_type="mob",
        actions=["trade", "talk"],
        quest_data=QuestData(enquire=True, complete=False),
    )

    wandering_bard = Char(
        id=102,
        key="mob.102",
        name="Lyria",
        template_id=3,
        title="the Bard",
        description="A young woman with a lute slung across her back, her eyes bright with curiosity.",
        room_description="Lyria the Bard sits on the fountain's edge, strumming her lute.",
        archetype="mage",
        core_faction="elf",
        state="sitting",
        stance="normal",
        health=60,
        health_max=60,
        mana=100,
        level=7,
        gender="female",
        pronouns="she/her",
        keywords="lyria bard elf female mob.102",
        keyword="lyria",
        display_faction="Wanderers",
        char_type="mob",
        actions=["listen", "talk"],
    )

    other_player = Char(
        id=200,
        key="player.200",
        name="Thorin",
        title="the Brave",
        description="A dwarf warrior clad in heavy armor, his beard braided with silver rings.",
        room_description="Thorin the Brave is here, resting against his warhammer.",
        archetype="warrior",
        core_faction="dwarf",
        state="standing",
        stance="defensive",
        health=180,
        health_max=200,
        mana=30,
        level=12,
        gender="male",
        keywords="thorin brave dwarf male player.200",
        keyword="thorin",
        display_faction="Stoneguard",
        char_type="player",
        is_linkless=False,
    )

    # --- Mock Rooms for Map ---

    room_town_square = MapRoom(
        key="room.1",
        x=0, y=0, z=0,
        type="indoor",
        color="#8B4513",
        north="room.2",
        east="room.3",
        south="room.4",
        west="room.5",
    )

    room_north_gate = MapRoom(
        key="room.2",
        x=0, y=1, z=0,
        type="road",
        south="room.1",
        north="room.6",
    )

    room_market = MapRoom(
        key="room.3",
        x=1, y=0, z=0,
        type="indoor",
        color="#DAA520",
        west="room.1",
        east="room.7",
    )

    room_south_road = MapRoom(
        key="room.4",
        x=0, y=-1, z=0,
        type="road",
        north="room.1",
        south="room.8",
    )

    room_tavern = MapRoom(
        key="room.5",
        x=-1, y=0, z=0,
        type="indoor",
        color="#654321",
        east="room.1",
        up="room.9",
    )

    room_forest_path = MapRoom(
        key="room.6",
        x=0, y=2, z=0,
        type="path",
        color="#228B22",
        south="room.2",
    )

    room_smithy = MapRoom(
        key="room.7",
        x=2, y=0, z=0,
        type="indoor",
        color="#B22222",
        west="room.3",
    )

    room_city_gate = MapRoom(
        key="room.8",
        x=0, y=-2, z=0,
        type="road",
        north="room.4",
        south_door_state="closed",
    )

    room_tavern_upstairs = MapRoom(
        key="room.9",
        x=-1, y=0, z=1,
        type="indoor",
        color="#654321",
        down="room.5",
    )

    mock_map = [
        room_town_square, room_north_gate, room_market, room_south_road,
        room_tavern, room_forest_path, room_smithy, room_city_gate,
        room_tavern_upstairs,
    ]

    # --- Current Room (Full Detail) ---

    current_room = Room(
        key="room.1",
        id=1,
        name="Town Square",
        description=(
            "You stand in the heart of the town, a bustling square surrounded by "
            "timber-framed buildings. A large stone fountain dominates the center, "
            "its waters sparkling in the sunlight. Merchants hawk their wares from "
            "colorful carts, while townsfolk go about their daily business. "
            "The cobblestones are worn smooth from centuries of foot traffic."
        ),
        color="#8B4513",
        x=0, y=0, z=0,
        type="indoor",
        zone=Zone(key="zone.1", name="Riverside Town"),
        inventory=[
            Item(
                key="item.200",
                name="wooden crate",
                cf_name="Wooden crate",
                type="container",
                is_container=True,
                is_pickable=False,
                description="A sturdy wooden crate, slightly open.",
                ground_description="A wooden crate sits against the wall.",
                keywords="wooden crate container",
                keyword="crate",
            ),
            health_potion,
        ],
        chars=[town_guard, merchant, wandering_bard, other_player],
        actions=["rest", "search"],
        details=["fountain", "carts", "buildings", "cobblestones"],
        houses=[
            House(direction="west", name="The Rusty Nail Tavern", key="room.5"),
        ],
        hint=None,
        flags=[],
        north="room.2",
        east="room.3",
        south="room.4",
        west="room.5",
    )

    # --- Player Skills ---

    player_skills = PlayerSkills(
        custom={},
    )

    # --- Player Aliases ---

    player_aliases = {
        "k": Alias(id=1, match="k", replacement="kill"),
        "l": Alias(id=2, match="l", replacement="look"),
        "n": Alias(id=3, match="n", replacement="north"),
        "s": Alias(id=4, match="s", replacement="south"),
        "e": Alias(id=5, match="e", replacement="east"),
        "w": Alias(id=6, match="w", replacement="west"),
    }

    # --- Actor (Player) ---

    mock_actor = Actor(
        id=player_id,
        key=f"player.{player_id}",
        name=player_name,
        title="the Wanderer",
        level=8,
        gender="male",
        keywords=f"{player_name.lower()} wanderer human male player.{player_id}",
        keyword=player_name.lower(),
        description="A weathered traveler with keen eyes and a determined expression.",
        archetype="warrior",
        core_faction="human",
        display_faction="Adventurers Guild",
        room={"key": current_room.key},  # Reference format {key: "room.xxx"}
        home=None,
        state="standing",
        stance="normal",
        # Vitals
        health=145,
        health_max=180,
        health_regen=5,
        stamina=75,
        stamina_max=100,
        stamina_regen=8,
        mana=40,
        mana_max=60,
        mana_regen=3,
        # Combat
        target=None,
        damage=35,
        armor=45,
        armor_perc=15.5,
        focus=None,
        # Stats
        strength=18,
        constitution=16,
        dexterity=12,
        intelligence=10,
        attack_power=42,
        spell_power=15,
        crit=8,
        crit_perc=5.2,
        dodge=6,
        dodge_perc=4.1,
        resilience=10,
        resilience_perc=6.8,
        # Equipment & inventory
        equipment=player_equipment,
        inventory=[health_potion, bread_loaf],
        # Progression
        experience=12500,
        experience_progress=2500,
        experience_needed=5000,
        gold=347,
        glory=125,
        medals=3,
        currencies={"tokens": 15, "gems": 2},
        # Grouping
        group_id=None,
        # Factions
        factions={
            "core": "human",
            "adventurers": 150,
            "merchants": 50,
        },
        # Skills
        skills=player_skills,
        trophy={1: 5, 2: 3, 3: 1},  # mob_template_id -> kill count
        # Player flags
        is_builder=False,
        is_immortal=False,
        is_temporary=False,
        is_invisible=False,
        is_idle=False,
        is_muted=False,
        is_staff=False,
        is_confirmed=True,
        link_id=None,
        name_recognition=True,
        player_housing=False,
        nochat=False,
        idle_logout=True,
        autoflee=0,
        room_description=f"{player_name} the Wanderer stands here, ready for adventure.",
        aliases=player_aliases,
        marks={"visited_castle": True, "met_king": False},
        clan={"name": "Silver Blades", "rank": "Member"},
        mute_list=None,
        channels="chat global",
        effects={},
        cooldowns={},
        user_id=1,
        user_name="player@example.com",
        config={"use_grapevine": False},
        language_proficiency=None,
    )

    # --- World Configuration ---

    mock_factions = {
        "human": Faction(
            code="human",
            name="Human",
            is_core=True,
            is_default=True,
            ranks=[
                FactionRank(standing=0, name="Outsider", number=1),
                FactionRank(standing=100, name="Citizen", number=2),
                FactionRank(standing=500, name="Respected", number=3),
                FactionRank(standing=1000, name="Champion", number=4),
            ],
        ),
        "elf": Faction(
            code="elf",
            name="Elf",
            is_core=True,
            is_default=False,
            ranks=[
                FactionRank(standing=0, name="Stranger", number=1),
                FactionRank(standing=100, name="Friend", number=2),
                FactionRank(standing=500, name="Ally", number=3),
            ],
        ),
        "dwarf": Faction(
            code="dwarf",
            name="Dwarf",
            is_core=True,
            is_default=False,
            ranks=[
                FactionRank(standing=0, name="Outsider", number=1),
                FactionRank(standing=100, name="Kinsman", number=2),
                FactionRank(standing=500, name="Clanfriend", number=3),
            ],
        ),
        "adventurers": Faction(
            code="adventurers",
            name="Adventurers Guild",
            is_core=False,
            is_default=False,
            ranks=[
                FactionRank(standing=0, name="Initiate", number=1),
                FactionRank(standing=100, name="Member", number=2),
                FactionRank(standing=300, name="Veteran", number=3),
                FactionRank(standing=600, name="Champion", number=4),
                FactionRank(standing=1000, name="Legend", number=5),
            ],
        ),
    }

    mock_currencies = {
        "1": Currency(code="gold", name="Gold", is_default=True),
        "2": Currency(code="tokens", name="Guild Tokens", is_default=False),
        "3": Currency(code="gems", name="Soul Gems", is_default=False),
    }

    mock_socials = Socials(
        cmds={
            "wave": [
                "You wave.",
                "{actor} waves.",
                "You wave at {target}.",
                "{actor} waves at you.",
                "{actor} waves at {target}.",
            ],
            "bow": [
                "You bow gracefully.",
                "{actor} bows gracefully.",
                "You bow before {target}.",
                "{actor} bows before you.",
                "{actor} bows before {target}.",
            ],
            "laugh": [
                "You laugh.",
                "{actor} laughs.",
                "You laugh at {target}.",
                "{actor} laughs at you.",
                "{actor} laughs at {target}.",
            ],
        },
        order=["wave", "bow", "laugh"],
    )

    # Skills - custom skill definitions (WR2: no core/flex/feat system)
    mock_skills: Dict[str, Any] = {}

    mock_world = World(
        id=world_id,
        key=f"world.{world_id}",
        name=world_name,
        context=f"world.{world_id}",
        instance_of=None,
        instance_ref=None,
        is_multiplayer=True,
        tier=1,
        leader=None,
        never_reload=False,
        has_corpse_decay=True,
        auto_equip=True,
        globals_enabled=True,
        classless=False,
        starting_room="room.1",
        death_room="room.1",
        death_mode="flee",
        death_route="",
        death_gold_penalty=0.1,
        allow_combat=True,
        allow_pvp=False,
        flee_to_unknown_rooms=False,
        players_can_set_title=True,
        factions=mock_factions,
        skills=mock_skills,
        facts={"world_started": True, "event_active": False},
        socials=mock_socials,
        currencies=mock_currencies,
    )

    # --- Who List ---

    mock_who_list = [
        WhoListEntry(
            key=f"player.{player_id}",
            name=player_name,
            title="the Wanderer",
            level=8,
            gender="male",
            is_immortal=False,
            is_invisible=False,
            is_idle=False,
            is_linkless=False,
            display_faction="Adventurers Guild",
            clan={"name": "Silver Blades", "rank": "Member"},
        ),
        WhoListEntry(
            key="player.200",
            name="Thorin",
            title="the Brave",
            level=12,
            gender="male",
            is_immortal=False,
            is_invisible=False,
            is_idle=False,
            is_linkless=False,
            display_faction="Stoneguard",
            clan=None,
        ),
        WhoListEntry(
            key="player.300",
            name="Elara",
            title="the Wise",
            level=15,
            gender="female",
            is_immortal=True,
            is_invisible=False,
            is_idle=True,
            is_linkless=False,
            display_faction="Council of Mages",
            clan={"name": "Arcane Order", "rank": "Elder"},
        ),
    ]

    # --- Assemble Final Payload ---

    return StateSyncData(
        map=mock_map,
        actor=mock_actor,
        room=current_room,
        world=mock_world,
        who_list=mock_who_list,
    )
