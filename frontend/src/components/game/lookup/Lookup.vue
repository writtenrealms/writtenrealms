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
import { buildCharActions } from "@/core/charActions";
import {
  findEquipmentItemByKey,
  findItemByKey,
  getItemKeyword,
  normalizeLookupItem,
} from "@/core/itemActions";
import LookupItem from '@/components/game/lookup/LookupItem.vue';
import LookupChar from '@/components/game/lookup/LookupChar.vue';
import LookupNotFound from '@/components/game/lookup/LookupNotFound.vue';

const store = useStore();

const lookup = computed(() => store.state.game.lookup);

const getKeyType = (key: string) => (key ? key.split(".")[0] : "");

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

const normalizeLookupChar = (sourceChar: any) => {
  const char = _.cloneDeep(sourceChar || {});

  if (!char.keywords) char.keywords = "";
  if (!char.keyword) char.keyword = getItemKeyword(char);
  char.actions = buildCharActions(
    char,
    store.state.game.player,
    store.state.game.world
  );

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
    return normalizeLookupItem(source, store.state.game);
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
