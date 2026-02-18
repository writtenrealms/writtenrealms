from config import constants as adv_consts

from config import game_settings as adv_config

from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models

from core.db import list_to_choice, optional


def get_auto_keywords(self):
    """
    Automatically gets the keywords for an instance based on its
    template's name, or its own.
    """
    if self.template:
        name = self.template.name
    else:
        name = self.name
    keywords = ' '.join(list(reversed([
        token.lower() for token in name.split(' ')
        if token not in adv_consts.EXCLUDE_NAME_TOKENS
    ])))
    return name


class CharMixin(models.Model):

    level = models.IntegerField(default=1)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    title = models.TextField(blank=True)

    archetype = models.TextField(
        choices=list_to_choice(adv_consts.ARCHETYPES),
        default=adv_consts.ARCHETYPE_WARRIOR,
        blank=True)
    gender = models.TextField(
        choices=list_to_choice(adv_consts.GENDERS),
        default=adv_consts.GENDER_FEMALE,
        **optional)
    experience = models.IntegerField(default=1)

    health = models.IntegerField(default=1)
    mana = models.IntegerField(default=0)
    stamina = models.IntegerField(default=0)

    group_id = models.TextField(**optional)

    gold = models.IntegerField(default=0)

    faction_assignments = GenericRelation(
        'builders.FactionAssignment',
        content_type_field='member_type',
        object_id_field='member_id')

    # Dictionary of skills currently learned
    skills = models.TextField(**optional)

    class Meta:
        abstract = True

    @staticmethod
    def post_char_save(sender, **kwargs):
        from spawns.models import Equipment
        if kwargs.get('created'):
            instance = kwargs['instance']
            eq = Equipment.objects.create()
            kwargs['instance'].equipment = eq
            kwargs['instance'].save()

    @staticmethod
    def post_char_delete(sender, **kwargs):
        if kwargs['instance'].equipment:
            kwargs['instance'].equipment.delete()

    @property
    def pronouns(self):
        """
        - Subject pronoun (he, she, it, they)
        - Object pronoun (him, her it, them)
        - Possessive adjective (his, her, its, their)
        - Possessive pronoun (his, hers, -, theirs)
        - Reflexive pronoun (himself, herself, itself, themselves)
        """
        if self.gender == adv_consts.GENDER_MALE:
            return ('he', 'him', 'his', 'his', 'himself')
        elif self.gender == adv_consts.GENDER_FEMALE:
            return ('she', 'her', 'her', 'hers', 'herself')
        elif self.gender == adv_consts.GENDER_NON_BINARY:
            return ('they', 'them', 'their', 'theirs', 'themselves')
        return ('it', 'it', 'its', '-', 'itself')

    @property
    def factions(self):
        from builders.models import Faction

        prefetched = getattr(self, '_prefetched_objects_cache', {})
        assignments = prefetched.get('faction_assignments')
        if assignments is None:
            assignments = list(
                self.faction_assignments.select_related('faction').all())

        # Get the core faction; if there are accidental duplicates, keep the
        # first assignment (oldest, given BaseModel default ordering).
        core = None
        for assignment in assignments:
            faction = assignment.faction
            if faction and faction.is_core:
                core = faction.code
                break

        # Fall back to the world's default core faction.
        if core is None:
            context_world = self.world.context if self.world.context else self.world
            core_factions = Faction.objects.filter(
                world=context_world,
                is_core=True,
                is_selectable=True)
            default_faction = core_factions.filter(is_default=True).first()
            if default_faction:
                core = default_faction.code
            elif core_factions:
                core = core_factions.first().code

        factions = {'core': core} if core else {}

        # Get the other factions.
        for assignment in assignments:
            faction = assignment.faction
            if faction and not faction.is_core:
                factions[faction.code] = assignment.value

        return factions

    @property
    def display_faction(self):
        """
        Heuristic display function for a character's main function,
        assuming that we simply display nothing if they are a human.
        """

        prefetched = getattr(self, '_prefetched_objects_cache', {})
        assignments = prefetched.get('faction_assignments')
        if assignments is None:
            assignments = list(
                self.faction_assignments.select_related('faction').all())

        # If we have a non-human core race, return that.
        for assignment in assignments:
            faction = assignment.faction
            if faction and faction.is_core and faction.code != 'human':
                return faction.name

        # If we belong to any non-core faction, return the highest standing
        # for a clanned faction.
        best_assignment = None
        for assignment in assignments:
            faction = assignment.faction
            if not faction or faction.is_core or assignment.value < 100:
                continue
            if not best_assignment or assignment.value > best_assignment.value:
                best_assignment = assignment
        if best_assignment:
            return best_assignment.faction.name

        return ''

    def sanitize(self):
        """
        Goes over character data and makes sure that everything is basically
        correct.
        """
        eq_items_pks = []
        for slot in adv_consts.EQUIPMENT_SLOTS:
            eq_item = getattr(self.equipment, slot, None)
            if eq_item:

                # If the equipped item is pending deletion, we null out
                # that reference
                if eq_item.is_pending_deletion:
                    setattr(self.equipment, slot, None)
                    continue

                # Make sure every item that is equipped is actually in the
                # equipment inventory
                if eq_item.container != self.equipment:
                    print("Correcting %s container..." % eq_item)
                    eq_item.container = self.equipment
                    eq_item.save(update_fields=['container_id', 'container_type'])

                eq_items_pks.append(eq_item.pk)

        # Make sure any items marked as belonging to the equipment inventory
        # but that was not seen in equipment is returned to belonging
        # simply to the char inventory.
        qs = self.equipment.inventory.exclude(pk__in=eq_items_pks)
        if qs:
            print("Correcting %s items marked as eq inv but not eqed" % (
                qs.count()))
            for item in qs:
                print("Marking %s as being contained in %s" % (item, self))
                item.container = self
                item.save(update_fields=['container_id', 'container_type'])


class MobMixin(models.Model):

    # Beast, Humanoid, Plant, Undead
    type = models.TextField(choices=list_to_choice(adv_consts.MOB_TYPES),
                            default=adv_consts.MOB_TYPE_BEAST)

    room_description = models.TextField(**optional)

    keywords = models.TextField(**optional)

    exp_worth = models.PositiveIntegerField(default=1)

    roaming_type = models.TextField(
        choices=list_to_choice(adv_consts.ROAM_OPTIONS),
        default=adv_consts.ROAM_STATIC)
    roam_chance = models.PositiveIntegerField(default=0)

    alignment = models.IntegerField(default=0)
    aggression = models.TextField(
        choices=list_to_choice(adv_consts.MOB_AGGRESSION_OPTIONS),
        default=adv_consts.MOB_AGGRESSION_PASSIVE)

    fights_back = models.BooleanField(default=True)
    is_invisible = models.BooleanField(default=False)

    is_crafter = models.BooleanField(default=False)

    is_upgrader = models.BooleanField(default=False)
    upgrade_cost_multiplier = models.FloatField(default=1.0)

    teaches = models.TextField(**optional)
    teaching_conditions = models.TextField(**optional)
    # 'all' - or space-delimited list of skills the mob unlearns
    unlearns = models.TextField(**optional)
    unlearn_cost = models.PositiveIntegerField(default=0)

    traits = models.TextField(**optional)


    # Use for warzones
    control_flag = models.TextField(**optional) # example: 'north_control'

    use_abilities = models.BooleanField(default=False)
    combat_script = models.TextField(**optional)

    flags = models.TextField(**optional)

    hit_msg_first = models.TextField(default='hit', blank=True)
    hit_msg_third = models.TextField(default='hits', blank=True)

    # points
    health_max = models.IntegerField(default=30)
    health_regen = models.IntegerField(default=0)  # %
    stamina_max = models.IntegerField(default=50)
    stamina_regen = models.IntegerField(default=0)  # %
    mana_max = models.IntegerField(default=1)
    mana_regen = models.IntegerField(default=0)  # %

    # attributes
    armor = models.PositiveIntegerField(default=0)
    dodge = models.PositiveIntegerField(default=0)
    crit = models.PositiveIntegerField(default=0)
    resilience = models.PositiveIntegerField(default=0)
    attack_power = models.PositiveIntegerField(default=1)
    spell_power = models.PositiveIntegerField(default=0)

    regen_rate = models.IntegerField(default=4)

    # Multipliers
    craft_multiplier = models.FloatField(
        default=adv_config.CRAFTER_MULTIPLIER)  # multiplier on cost
    merchant_profit = models.FloatField(
        default=adv_config.MERCHANT_PROFITS) # multiplier on merchants

    class Meta:
        abstract = True

    get_auto_keywords = get_auto_keywords


class ItemMixin(models.Model):

    level = models.IntegerField(default=1)
    name = models.TextField(default='Unnamed Item')
    description = models.TextField(**optional)

    ground_description = models.TextField(**optional)

    keywords = models.TextField(**optional)

    type = models.TextField(choices=list_to_choice(adv_consts.ITEM_TYPES),
                            default=adv_consts.ITEM_TYPE_INERT)

    # Container options
    is_persistent = models.BooleanField(default=False)
    capacity = models.IntegerField(default=0)

    quality = models.TextField(
        choices=list_to_choice(adv_consts.ITEM_QUALITIES),
        default=adv_consts.ITEM_QUALITY_NORMAL)

    is_boat = models.BooleanField(default=False)
    is_pickable = models.BooleanField(default=True)

    cost = models.PositiveIntegerField(default=0)
    # currency = models.TextField(
    #     choices=list_to_choice(adv_consts.ITEM_CURRENCIES),
    #     default=adv_consts.ITEM_CURRENCY_GOLD)
    currency = models.ForeignKey(
        'builders.Currency',
        on_delete=models.SET_NULL,
        **optional)

    # Food fields
    food_value = models.IntegerField(default=0)
    food_type = models.TextField(
        choices=list_to_choice(adv_consts.ITEM_FOOD_TYPES),
        **optional)

    # Equipment fields

    equipment_type = models.TextField(
        choices=list_to_choice(adv_consts.EQUIPMENT_TYPES),
        **optional)

    # Armor
    armor_class = models.TextField(
        choices=list_to_choice(adv_consts.ARMOR_CLASSES),
        default=adv_consts.ARMOR_CLASS_LIGHT)

    # Weapons
    weapon_grip = models.TextField(
        choices=list_to_choice(adv_consts.WEAPON_GRIPS),
        default=adv_consts.WEAPON_GRIP_ONE_HAND)
    hit_msg_first = models.TextField(default='hit', blank=True)
    hit_msg_third = models.TextField(default='hits', blank=True)
    weapon_type = models.TextField(
        choices=list_to_choice(adv_consts.WEAPON_TYPES),
        **optional)

    skill_modifier = models.TextField(**optional)
    on_use_cmd = models.TextField(**optional)
    on_use_description = models.TextField(**optional)
    on_use_equipped = models.BooleanField(default=False)

    # Points
    health_max = models.IntegerField(default=0)
    health_regen = models.IntegerField(default=0)
    mana_max = models.IntegerField(default=0)
    mana_regen = models.IntegerField(default=0)
    stamina_max = models.IntegerField(default=0)
    stamina_regen = models.IntegerField(default=0)

    # Base stats
    strength = models.IntegerField(default=0)
    constitution = models.IntegerField(default=0)
    dexterity = models.IntegerField(default=0)
    intelligence = models.IntegerField(default=0)

    # Computed Stats
    attack_power = models.IntegerField(default=0)
    spell_power = models.IntegerField(default=0)
    resilience = models.IntegerField(default=0)
    dodge = models.IntegerField(default=0)
    crit = models.IntegerField(default=0)

    class Meta:
        abstract = True

    get_auto_keywords = get_auto_keywords
