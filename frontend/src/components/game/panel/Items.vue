<template>
  <div>
    <div class="inventory-view">
      <div
        class="inventory-item"
        :class="[item.quality]"
        v-for="item in inventory"
        :key="item.key"
      >
        <span
          v-interactive="{
            target: item,
            primaryAction: true,
            actionContext: 'inventory',
          }"
          class="interactive"
        >{{ item.name }}</span>
        <span class="item-count" v-if="item.count && item.count > 1">&nbsp;[{{item.count}}]</span>
      </div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from 'vuex';
import { stackedInventory } from "@/core/utils";

const store = useStore();

const player = computed(() => store.state.game.player);
const inventory = computed(() => stackedInventory(player.value.inventory));
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
.inventory-view {
  padding: 0 20px;
}
</style>
