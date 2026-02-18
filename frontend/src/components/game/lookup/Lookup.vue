<template>
  <div v-if="entity" id="lookup" :class="{ 'lookup-mobile': store.state.game.is_mobile }">
    <component :is="lookupComponent()" :entity="entity" class="lookup" :lookup="lookup"></component>
  </div>
</template>

<script lang='ts' setup>
/*
  Component to display more information about an item or mob that is being
  hovered over (desktop) or clicked on (mobile).
*/
import { computed } from 'vue';
import { useStore } from 'vuex';
import _ from 'lodash';
import LookupItem from '@/components/game/lookup/LookupItem.vue';
import LookupChar from '@/components/game/lookup/LookupChar.vue';
import LookupNotFound from '@/components/game/lookup/LookupNotFound.vue';

const store = useStore();

const lookup = computed(() => store.state.game.lookup);

const EQUIPMENT_SLOTS = [
  "weapon",
  "offhand",
  "head",
  "body",
  "arms",
  "hands",
  "waist",
  "legs",
  "feet",
  "accessory",
];

const ITEM_ACTIONS = [
  "get",
  "drop",
  "wield",
  "remove",
  "wear",
  "get_from",
  "get_all_from",
  "sell",
  "buy",
  "upgrade",
  "eat",
  "use",
];

const CHAR_ACTIONS = [
  "kill",
  "enquire",
  "list",
  "offer",
  "complete",
  "craft",
  "upgrade",
  "follow",
  "unfollow",
  "group",
];

const getKeyType = (key: string) => (key ? key.split(".")[0] : "");

const getKeyword = (entity: any) => {
  if (entity && entity.keyword) return entity.keyword;
  if (!entity || !entity.keywords) return "";
  return entity.keywords.split(" ")[0];
};

const actionsAsMap = (
  actions: any,
  knownActions: string[]
): Record<string, any> => {
  const mapped: Record<string, any> = {};
  for (const action of knownActions) {
    mapped[action] = false;
  }

  if (Array.isArray(actions)) {
    for (const action of actions) {
      mapped[action] = true;
    }
    return mapped;
  }

  if (actions && typeof actions === "object") {
    for (const [action, value] of Object.entries(actions)) {
      mapped[action] = value;
    }
  }

  return mapped;
};

const findItemByKey = (items: any[], key: string): any => {
  if (!items) return null;

  for (const item of items) {
    if (!item) continue;
    if (item.key === key) return item;
    const nested = findItemByKey(item.inventory || [], key);
    if (nested) return nested;
  }

  return null;
};

const findEquipmentItemByKey = (equipment: any, key: string): any => {
  if (!equipment) return null;

  for (const slot of EQUIPMENT_SLOTS) {
    const item = equipment[slot];
    if (!item) continue;
    if (item.key === key) return item;
    const nested = findItemByKey(item.inventory || [], key);
    if (nested) return nested;
  }

  return null;
};

const findEntityByKey = (key: string): any => {
  const keyType = getKeyType(key);
  const gameState = store.state.game;

  if (keyType === "mob" || keyType === "player") {
    const roomChars = gameState.room && gameState.room.chars ? gameState.room.chars : [];
    const roomChar = roomChars.find((char) => char.key === key);
    if (roomChar) return roomChar;

    if (gameState.player && gameState.player.key === key) {
      return gameState.player;
    }

    return null;
  }

  if (keyType !== "item") {
    return null;
  }

  if (gameState.player && gameState.player.inventory) {
    const playerItem = findItemByKey(gameState.player.inventory, key);
    if (playerItem) return playerItem;
  }

  if (gameState.player && gameState.player.equipment) {
    const equippedItem = findEquipmentItemByKey(gameState.player.equipment, key);
    if (equippedItem) return equippedItem;
  }

  if (gameState.room && gameState.room.inventory) {
    const roomItem = findItemByKey(gameState.room.inventory, key);
    if (roomItem) return roomItem;
  }

  if (gameState.room && gameState.room.chars) {
    for (const char of gameState.room.chars) {
      const eqItem = findEquipmentItemByKey(char.equipment, key);
      if (eqItem) return eqItem;
    }
  }

  return null;
};

const isTopLevelItem = (items: any[], key: string) => {
  if (!items) return false;
  return items.some((item) => item && item.key === key);
};

const getContainerKey = (item: any) => {
  const inContainer = item && item.in_container;
  if (!inContainer) return "";
  if (typeof inContainer === "string") return inContainer;
  return inContainer.key || "";
};

const normalizeItemActions = (item: any) => {
  const actions = actionsAsMap(item.actions, ITEM_ACTIONS);
  const gameState = store.state.game;
  const room = gameState.room || {};
  const player = gameState.player || {};
  const roomChars = room.chars || [];

  const inRoom = isTopLevelItem(room.inventory || [], item.key);
  const inInventory = isTopLevelItem(player.inventory || [], item.key);
  const inEquipment =
    !inInventory && Boolean(findEquipmentItemByKey(player.equipment, item.key));

  const isContainer =
    item.is_container === true || item.type === "container" || item.type === "corpse";

  const hasMerchant = roomChars.some((char) => char && char.is_merchant);
  const hasUpgrader = roomChars.some((char) => char && char.is_upgrader);

  if (inRoom) {
    if (item.is_pickable !== false) actions.get = true;
    if (isContainer) actions.get_from = true;
  } else if (inInventory) {
    actions.drop = true;

    if (item.type === "equippable") {
      if (item.equipment_type && item.equipment_type.startsWith("weapon")) {
        actions.wield = true;
      } else if (item.equipment_type) {
        actions.wear = true;
      }
    }

    if (isContainer) actions.get_from = true;
    if (item.type === "food") actions.eat = true;
    if (item.on_use_cmd) actions.use = true;

    const currency = (item.currency || "").toLowerCase();
    if ((currency === "gold" || !currency) && item.cost && hasMerchant) {
      actions.sell = true;
    }

    if (hasUpgrader) {
      const quality = (item.quality || "").toLowerCase();
      const upgradeCount = item.upgrade_count || 0;
      if (
        (quality === "imbued" && upgradeCount === 0) ||
        (quality === "enchanted" && upgradeCount <= 2)
      ) {
        actions.upgrade = true;
      }
    }
  } else if (inEquipment) {
    actions.remove = true;
    if (item.on_use_cmd) actions.use = true;
  }

  const containerKey = getContainerKey(item);
  if (containerKey.startsWith("mob.")) {
    const merchant = roomChars.find(
      (char) => char && char.key === containerKey && char.is_merchant
    );
    if (merchant) {
      actions.buy = true;
      if (!item.buy_price && item.cost) {
        const profit = merchant.merchant_profit || 2;
        item.buy_price = Math.round(item.cost * profit);
      }
    }
  } else if (!inRoom && !inInventory && !inEquipment && hasMerchant && item.cost) {
    actions.buy = true;
  }

  return actions;
};

const normalizeLookupItem = (sourceItem: any) => {
  const item = _.cloneDeep(sourceItem || {});

  if (!item.keywords) item.keywords = "";
  if (!item.keyword) item.keyword = getKeyword(item);
  if (!item.description) {
    item.description = item.name ? `It is ${item.name}.` : "It is an item.";
  }
  if (item.is_container === undefined) {
    item.is_container = item.type === "container" || item.type === "corpse";
  }
  if (!Array.isArray(item.inventory)) item.inventory = [];
  item.actions = normalizeItemActions(item);

  return item;
};

const normalizeLookupChar = (sourceChar: any) => {
  const char = _.cloneDeep(sourceChar || {});

  if (!char.keywords) char.keywords = "";
  if (!char.keyword) char.keyword = getKeyword(char);

  const actions = actionsAsMap(char.actions, CHAR_ACTIONS);
  const questData = char.quest_data || {};
  if (questData.complete) actions.complete = true;
  if (questData.enquire) actions.enquire = true;
  if (char.is_merchant) {
    actions.list = true;
    actions.offer = true;
  }
  if (char.is_crafter) actions.craft = true;
  if (char.is_upgrader) actions.upgrade = true;
  if (char.completion_action && !actions.completion_action) {
    actions.completion_action = char.completion_action;
  }
  char.actions = actions;

  return char;
};

const entity = computed(() => {
  const activeLookup = lookup.value;
  if (!activeLookup || !activeLookup.key) return null;

  const key = activeLookup.key;
  const keyType = getKeyType(key);
  const source = activeLookup.entity || findEntityByKey(key);

  if (!source) {
    return "error";
  }

  if (keyType === "item") {
    return normalizeLookupItem(source);
  }

  if (keyType === "mob" || keyType === "player") {
    return normalizeLookupChar(source);
  }

  return _.cloneDeep(source);
});

const lookupComponent = () => {
  if (entity.value === "error") {
    return LookupNotFound;
  }

  const lookupKey = (entity.value && entity.value.key) || (lookup.value && lookup.value.key) || "";
  const type = getKeyType(lookupKey);
  if (type === "item") {
    return LookupItem;
  }
  return LookupChar;
};
</script>

<style lang="scss">
@import "@/styles/colors.scss";

#lookup {
  width: 300px;
  padding: 10px;
  border: 3px solid $color-background-very-light;
  background: $color-background-light;

  &.lookup-mobile {
    border: 0;
    background: none;
    margin: 20px;

    .lookup {
      padding: 20px;
      background: $color-background-black;
    }
  }
}
</style>
