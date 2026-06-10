<template>
  <div>
    <div class="name" :class="[item.quality]">
      {{ capfirst(item.name) }}
      <span class='ml-2 color-text-50 font-text-light' v-if="item.template_id && player.is_builder">
        [ {{ item.template_id }} ]
      </span>
    </div>
    <div class="summary">{{ summary }}</div>

    <div
      class="cannot-eq-heavy-armor"
      v-if="cannot_wear_heavy_armor"
    >Cannot equip {{ armorClassLabel(item.armor_class).toLowerCase() || "this armor" }}.</div>

    <div
      class="level-too-high"
      v-if="item.type === 'equippable' && is_eq_item_too_high_level"
    >Can only wear items up to level {{ is_eq_item_too_high_level }}.</div>

    <div
      class="level-too-high"
      v-if="item.type === 'food' && item.level > player.level"
    >Food is too high level to be consumed.</div>

    <div class="description">
      <div class="description-line" v-for="(line, index) in lines" :key="index">{{ line }}</div>
    </div>

    <template v-if="item.type === 'equippable' && itemStats.length">
      <div class="list-title stats" v-if="itemStats.length">Stats:</div>
      <table class="item-stats">
        <tr
          v-for="stat in itemStats"
          :key="itemStats.indexOf(stat)"
          :class="{ 'zero': stat.is_zero }"
        >
          <td class="item-name">{{ stat.label }}</td>
          <td class="item-value">{{ stat.value }}</td>
          <td class="item-value-change">
            <span class="change" :class="[stat.change_direction]">({{ stat.change }})</span>
          </td>
        </tr>
      </table>

      <div class='augments' v-if="item.augment && item.augment.key">
          Augmented with <span class="font-bold">{{ item.augment.name }}</span><span v-if="item.augment.stats && item.augment.stats.length">:</span><span v-else>.</span>
        <ul v-for="stat in (item.augment.stats || [])" :key="stat.stat" class="list">
          <li>+{{ stat.value }} {{ stat.stat }}</li>
        </ul>
      </div>

      <div
        class="upgrade_count color-text-50"
        v-if="item.upgrade_count > 0"
      >Upgrade count: {{item.upgrade_count}}</div>
    </template>

    <template v-else-if="item.type === 'container' || item.type === 'corpse'">
      <template v-if="item.inventory.length">
        <div class="container-info">
          <div
            class="list-title"
            v-if="item.inventory.length === 1"
          >{{ item.inventory.length }} item in {{ item.type }}:</div>
          <div
            class="list-title"
            v-else-if="item.inventory.length > 1"
          >{{ item.inventory.length }} items in {{ item.type }}:</div>

          <ul class="list">
            <li v-for="contained_item in inventoryStack" :key="contained_item.display_key" class="inventory-item">
              <span
                :class="{ [contained_item.quality]: true}"
                class="contained-item interactive"
                @click="onClickContainedItem(contained_item)"
                v-if="!from_lookup"
                v-interactive="{target: contained_item}"
              >{{ contained_item.name }}</span>
              <span
                v-else
                :class="{ [contained_item.quality]: true}"
                @click="onClickContainedItem(contained_item)"
                class="contained-item"
              >{{ contained_item.name }}</span>
              <span class="item-count" v-if="contained_item.count && contained_item.count > 1">&nbsp;[{{contained_item.count}}]</span>
            </li>
          </ul>
        </div>
      </template>
      <template v-else>Container is empty.</template>
    </template>

    <div class="color-text-50 mt-2" v-if="item.label">An item label reads: "{{ item.label }}"</div>

    <div class='color-primary' v-if="item.on_use_description">On Use: {{ item.on_use_description }}</div>
    <div class='color-primary' v-else-if="item.on_use_cmd">Item has On Use command.</div>

    <div v-if="item.cost" class="color-secondary mt-2">
      Sells for {{ item.cost }} {{ currencies[item.currency] }}.

      <template v-if="isUpgradable">
        <br />
         Upgrade can be attempted for {{ upgrade_cost(item) }} gold.
      </template>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { getTargetInGroup } from "@/core/utils.ts";
import { capfirst } from "@/core/utils.ts";
import { stackedInventory } from "@/core/utils.ts";

const store = useStore();

const props = defineProps({
  item: {
    type: Object,
    required: true
  },
  from_lookup: {
    type: Boolean,
    default: false
  }
});

const world = computed(() => store.state.game.world);
const resourceLabels = computed(() => world.value?.labels?.resources || {});
const statLabels = computed(() => world.value?.labels?.stats || {});
const attributeLabels = computed(() => world.value?.labels?.attributes || {});

const ITEM_STAT_LABELS = {
  weapon_damage: "Weapon damage",
  armor: "Armor",
  attack_power: "Attack power",
  ability_power: "Ability power",
  crit: "Crit",
  resilience: "Resilience",
  dodge: "Dodge",
  health_max: "Max health",
  health_regen: "Health regen",
  energy_max: "Max energy",
  energy_regen: "Energy regen",
  stamina_max: "Max stamina",
  stamina_regen: "Stamina regen",
};

const statLabel = (statName: string) => {
  if (statName === "ability_power") {
    return statLabels.value.ability_power || ITEM_STAT_LABELS[statName];
  }
  if (statName === "energy_max") {
    const energy = resourceLabels.value.energy || "Energy";
    return `Max ${energy}`;
  }
  if (statName === "energy_regen") {
    const energy = resourceLabels.value.energy || "Energy";
    return `${energy} Regen`;
  }
  if (attributeLabels.value[statName]) {
    return attributeLabels.value[statName];
  }
  if (statLabels.value[statName]) {
    return statLabels.value[statName];
  }
  if (ITEM_STAT_LABELS[statName]) return ITEM_STAT_LABELS[statName];
  const label = statName.replace(/_/g, " ");
  return capfirst(label);
};

const getStatValue = (item: any, statName: string) => {
  if (!item || item[statName] === undefined || item[statName] === null) return 0;
  const parsed = Number(item[statName]);
  if (Number.isNaN(parsed)) return 0;
  return Math.round(parsed);
};

const getAttributeValue = (item: any, statName: string) => {
  const values = item?.attributes || {};
  if (values[statName] === undefined || values[statName] === null) return 0;
  const parsed = Number(values[statName]);
  if (Number.isNaN(parsed)) return 0;
  return Math.round(parsed);
};

const buildComparedStats = (item: any) => {
  if (!item || item.type !== "equippable") return [];

  const eqType = item.equipment_type || "";
  let slot = eqType;
  if (eqType.startsWith("weapon")) slot = "weapon";
  else if (eqType === "shield") slot = "offhand";

  const playerEquipment = (store.state.game.player && store.state.game.player.equipment) || {};
  const equippedItem = playerEquipment[slot];
  const offhandItem = eqType === "weapon_2h" ? playerEquipment.offhand : null;
  const attributeOrder = world.value?.labels?.order?.attributes || Object.keys(item.attributes || {});
  const attributeStats: any[] = attributeOrder
    .filter((statName: string) => getAttributeValue(item, statName) || getAttributeValue(equippedItem, statName) || (offhandItem && getAttributeValue(offhandItem, statName)))
    .map((statName: string) => {
      const value = getAttributeValue(item, statName);
      let equippedValue = getAttributeValue(equippedItem, statName);
      if (offhandItem) {
        equippedValue += getAttributeValue(offhandItem, statName);
      }
      const delta = value - equippedValue;
      let change = "+0";
      let changeDirection = "neutral";
      if (delta > 0) {
        change = `+${delta}`;
        changeDirection = "positive";
      } else if (delta < 0) {
        change = `${delta}`;
        changeDirection = "negative";
      }
      return {
        name: statName,
        label: statLabel(statName),
        value,
        change,
        change_direction: changeDirection,
        is_zero: value === 0,
      };
    });

  const statOrder = [
    "weapon_damage",
    "armor",
    "attack_power",
    "ability_power",
    "crit",
    "resilience",
    "dodge",
    "health_max",
    "health_regen",
    "energy_max",
    "energy_regen",
    "stamina_max",
    "stamina_regen",
  ];

  const stats: any[] = [...attributeStats];
  for (const statName of statOrder) {
    if (statName === "weapon_damage" && slot !== "weapon") continue;
    if (statName === "armor" && slot === "weapon") continue;

    const value = getStatValue(item, statName);
    let equippedValue = getStatValue(equippedItem, statName);
    if (offhandItem) {
      equippedValue += getStatValue(offhandItem, statName);
    }

    if (!value && !equippedValue) continue;

    const delta = value - equippedValue;
    let change = "+0";
    let changeDirection = "neutral";
    if (delta > 0) {
      change = `+${delta}`;
      changeDirection = "positive";
    } else if (delta < 0) {
      change = `${delta}`;
      changeDirection = "negative";
    }

    stats.push({
      name: statName,
      label: statLabel(statName),
      value: value,
      change: change,
      change_direction: changeDirection,
      is_zero: value === 0,
    });
  }

  if (stats.length <= 1) return stats;
  const [primary, ...rest] = stats;
  const positives = rest.filter((stat: any) => stat.value > 0);
  const others = rest.filter((stat: any) => stat.value <= 0);
  return [primary, ...positives, ...others];
};

const rawStats = computed(() => {
  if (Array.isArray(props.item.stats)) return props.item.stats;
  return buildComparedStats(props.item);
});

const itemStats = computed(() => {
  return rawStats.value.filter(stat => world.value.allow_combat || stat.name !== 'weapon_damage');
});

const inventoryStack = computed(() => {
  const inventory = props.item.inventory || [];
  const inventoryCopy = inventory.map((invItem) => ({ ...invItem }));
  return stackedInventory(inventoryCopy);
});

const summary = computed(() => {
  if (props.item.summary) return props.item.summary;

  const itemType = props.item.type;
  const level = props.item.level || 1;
  const quality = props.item.quality || "normal";
  const qualityPrefix = quality !== "normal" ? `${quality} ` : "";

  if (itemType === "equippable") {
    const eqType = props.item.equipment_type || "";
    if (eqType === "weapon_1h") {
      return `Level ${level} ${qualityPrefix}${props.item.weapon_type || "weapon"}`.trim();
    }
    if (eqType === "weapon_2h") {
      return `Level ${level} ${qualityPrefix}two-handed ${props.item.weapon_type || "weapon"}`.trim();
    }
    if (eqType === "shield") {
      const armorClass = armorClassLabel(props.item.armor_class);
      const prefix = armorClass ? `${armorClass.toLowerCase()} ` : "";
      return `Level ${level} ${qualityPrefix}${prefix}shield`.trim();
    }
    const armorClass = armorClassLabel(props.item.armor_class);
    const prefix = armorClass ? `${armorClass.toLowerCase()} ` : "";
    return `Level ${level} ${qualityPrefix}${prefix}armor, worn on ${eqType}`.trim();
  }

  if (itemType === "inert") return "Item";
  if (itemType === "quest") return "Quest item";
  if (itemType === "food") {
    if (props.item.food_value && props.item.food_type) {
      return `Level ${level} consumable, restores ${props.item.food_value} ${props.item.food_type}`;
    }
    return `Level ${level} consumable`;
  }

  if (!itemType) return "Item";
  return capfirst(itemType);
});

const upgrader = computed(() => {
  const roomChars = (store.state.game.room && store.state.game.room.chars) || [];
  for (const char of roomChars) {
    if (char.is_upgrader) {
      return char;
    }
  }
  return null;
});

const isUpgradable = computed(() => {
  if (!upgrader.value) return false;

  // Only show upgrade option in workshop rooms
  // if (store.state.game.room.flags.indexOf("workshop") == -1) return false;

  // Show upgrade price based on whether they can be upgraded
  if (props.item.quality == "enchanted" && props.item.upgrade_count <= 2) return true;
  else if (props.item.quality == "imbued" && props.item.upgrade_count == 0) return true;
  return false;
});

const upgrade_cost = (item) => {
  return Math.ceil(item.upgrade_cost * upgrader.value.upgrade_cost_multiplier);
};

const lines = computed(() => {
  const description = props.item.description || `It is ${props.item.name}.`;
  return description.split("\n") || [];
});
const player = {
  ...(store.state.game.player || {}),
  marks: (store.state.game.player && store.state.game.player.marks) || {},
};
const armorClassEntries = computed(() => world.value?.equipment?.armor_classes || []);
const authoredArmorClasses = computed(() => armorClassEntries.value.length > 0);
const armorClassLabel = (armorClass: string) => {
  if (!armorClass) return "";
  const entry = armorClassEntries.value.find((candidate: any) => candidate.key === armorClass);
  return (entry && entry.label) || armorClass.replace(/_/g, " ");
};
const usesArmorProficiency = computed(() => {
  return [
    "head",
    "body",
    "arms",
    "hands",
    "waist",
    "legs",
    "feet",
    "shield",
  ].includes(props.item.equipment_type);
});
const playerArmorProficiencies = computed(() => {
  const proficiencies = world.value?.equipment?.armor_proficiencies || {};
  const classProficiencies = proficiencies.classes || {};
  if (Object.prototype.hasOwnProperty.call(classProficiencies, player.archetype)) {
    return classProficiencies[player.archetype];
  }
  if (Object.prototype.hasOwnProperty.call(proficiencies, "default")) {
    return proficiencies.default;
  }
  return null;
});
const is_eq_item_too_high_level = computed(() => {
  // If the user is allowed to wear the item, return false.
  // If the user cannot wear the item, return the max level
  // they are able to wear equipment at.
  // const delta = props.item.level - player.value.level;
  const delta = props.item.level - player.level;
  if (delta > 3) {
    return player.level + 3;
  } else {
    return false;
  }
});

const onClickContainedItem = (contained_item) => {
  // Get the selection string for the item we're getting
  const target = getTargetInGroup(contained_item, props.item.inventory);
  if (!target) return;

  // Get the selection string for the container item
  const inContainer = props.item.in_container;
  const container_key = typeof inContainer === "string" ? inContainer : inContainer && inContainer.key;
  if (!container_key) return;
  let container_group;
  if (container_key.includes("room.") || RegExp(/@\d+:room\./).test(container_key)) {
    // The container is a room, therefore look in the room's
    // inventory
    container_group = store.state.game.room.inventory;
  } else {
    // The container is a player
    container_group = store.state.game.player.inventory;
  }
  const container = getTargetInGroup(props.item, container_group);
  if (!container) return;

  store.dispatch("game/cmd", `get ${target} ${container}`);
};

const cannot_wear_heavy_armor = computed(() => {
  if (
    authoredArmorClasses.value &&
    usesArmorProficiency.value &&
    props.item.armor_class
  ) {
    const proficiencies = playerArmorProficiencies.value;
    return Array.isArray(proficiencies) && !proficiencies.includes(props.item.armor_class);
  }

  if (player.marks.heavy_armor_proficiency === 'true' ||
      player.marks.proficiency_heavy_armor === 'true')
    return false;

  return player.archetype !== 'warrior' && props.item.armor_class === 'heavy'
});

const currencies = computed(() => {
  const currencies = {}
  for (const currency_id of Object.keys(store.state.game.world.currencies)) {
    const currency_data = store.state.game.world.currencies[currency_id];
    currencies[currency_data.code] = currency_data.name;
  }
  return currencies;
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
.level-too-high,
.cannot-eq-heavy-armor {
  color: $color-red;
}

.description {
  max-height: 300px;
  overflow-y: auto;

  .description-line {
    min-height: 14px;
  }
}

.augments .list {
  margin-bottom: 0;
}
</style>
