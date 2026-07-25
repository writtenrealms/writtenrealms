<template>
  <div class="inventory-view indented">
    <div v-if="inventory.length === 1">You are carrying 1 item:</div>
    <div v-else-if="inventory.length === 0">You are not carrying any items.</div>
    <div v-else>You are carrying {{inventory.length}} items:</div>

    <ul class="list">
      <li v-for="item in inventoryStack" :key="item.display_key" class="inventory-item">
        <span
          v-if="isLastMessage && isCurrentInventoryItem(item)"
          v-interactive="{
            target: item,
            primaryAction: true,
            actionContext: 'inventory',
          }"
          class='interactive'
          :class="[item.quality]"
        >{{ item.name }}</span>
        <span v-else
          :class="[item.quality]"
        >{{ item.name }}</span>
        <span class="item-count" v-if="item.count && item.count > 1">&nbsp;[{{item.count}}]</span>
      </li>
    </ul>

    <div
      v-for="balance in walletEntries"
      :key="`currency-${balance.currency}`"
      class="wallet-inv"
    >
      You have {{ balance.display }}.
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { walletBalanceEntries } from "@/core/economy.ts";
import { stackedInventory } from "@/core/utils.ts";

const store = useStore();

const props = defineProps<{
  message: any;
}>();

const actor = computed(() => props.message?.data?.actor || {});
const world = computed(() => props.message?.data?.world || store.state.game.world || {});
const inventory = computed(() => actor.value.inventory || []);
const inventoryStack = computed(() => stackedInventory(inventory.value));
const currentInventoryKeys = computed(() => new Set(
  (store.state.game.player?.inventory || []).map((item: any) => item.key),
));
const isCurrentInventoryItem = (item: any) => (
  currentInventoryKeys.value.has(item.key)
);
const walletEntries = computed(() => walletBalanceEntries(
  world.value?.economy,
  actor.value?.economy,
));
const isLastMessage = computed(() => store.state.game.last_message[props.message.type] == props.message);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
