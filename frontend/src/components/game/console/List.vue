<template>
  <div class="merchant-list indented">
    <div v-if="stock.length">
      {{ capfirst(merchant.name || "The merchant") }} has for sale:
    </div>
    <div v-else>
      {{ capfirst(merchant.name || "The merchant") }} has nothing for sale.
    </div>

    <ol v-if="stock.length" class="list mt-4">
      <li
        v-for="entry in stock"
        :key="entry.key || entry.id"
        :value="entry.number"
        class="inventory-item"
      >
        <span
          v-if="isLastMessage && isCurrentMerchantRoom && isCurrentEntry(entry)"
          v-interactive="{
            target: interactiveItem(entry),
            primaryAction: true,
          }"
          class="interactive"
          :class="[entry.item?.quality]"
        >{{ entry.item?.name || "item" }}</span>
        <span v-else :class="[entry.item?.quality]">{{ entry.item?.name || "item" }}</span>
        for {{ formatPrice(entry.price) }}
      </li>
    </ol>
    <div
      v-if="props.message?.data?.truncated"
      class="color-text-50 font-text-light ml-2 mb-2"
    >
      Only the first {{ props.message?.data?.limit }} items are shown.
    </div>

    <div v-if="walletDisplay" class="wallet-inv color-secondary mt-4">
      You have {{ walletDisplay }}.
    </div>
    <div v-if="stock.length" class="purchase-hint color-text-50 font-text-light ml-2">
      {{ purchaseHint }}
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { formatMoney } from "@/core/economy.ts";
import type { Money } from "@/core/economy.ts";
import { capfirst } from "@/core/utils";
import {
  merchantProviderIsAvailableInRoom,
  merchantProviderTarget,
} from "@/core/merchantProviders";

const store = useStore();
const props = defineProps<{ message: any }>();

const merchant = computed(() => props.message?.data?.merchant || {});
const stock = computed(() => (
  Array.isArray(props.message?.data?.stock) ? props.message.data.stock : []
));
const purchaseHint = computed(() => (
  String(props.message?.data?.hint || "buy # to purchase an item")
));
const isLastMessage = computed(() => (
  store.state.game.last_message[props.message.type] === props.message
));
const isCurrentMerchantRoom = computed(() => (
  merchantProviderIsAvailableInRoom(merchant.value, store.state.game.room)
));
const currentInventoryKeys = computed(() => new Set(
  (store.state.game.player?.inventory || []).map((item: any) => item.key),
));
const isCurrentEntry = (entry: any) => (
  !currentInventoryKeys.value.has(entry.item?.key)
);
const economy = computed(() => store.state.game.world?.economy);
const settlementCurrency = computed(() => String(
  props.message?.data?.balance?.currency
  || props.message?.data?.funds?.currency
  || "",
));
const settlementBalance = computed(() => (
  props.message?.data?.balance?.amount
  ?? store.state.game.player?.economy?.balances?.[settlementCurrency.value]
  ?? 0
));
const walletDisplay = computed(() => settlementCurrency.value
  ? String(props.message?.data?.balance?.display || "").trim() || formatMoney(
    { amount: settlementBalance.value, currency: settlementCurrency.value },
    economy.value,
  )
  : "");

const formatPrice = (price: Money) => formatMoney(price, economy.value);

const buyCommand = (entry: any): string => {
  const merchantTarget = merchantProviderTarget(merchant.value);
  return merchantTarget
    ? `buy ${entry.key} from ${merchantTarget}`
    : `buy ${entry.key}`;
};

const interactiveItem = (entry: any) => ({
  ...(entry.item || {}),
  in_container: merchant.value,
  buy_price: entry.price,
  buy_command: buyCommand(entry),
  actions: Array.isArray(entry.item?.actions)
    ? [...entry.item.actions, "buy"]
    : { ...(entry.item?.actions || {}), buy: true },
});
</script>
