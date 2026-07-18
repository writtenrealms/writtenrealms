<template>
  <div class="merchant-list indented">
    <div v-if="stock.length">
      {{ capfirst(merchant.name || "The merchant") }} has for sale:
    </div>
    <div v-else>
      {{ capfirst(merchant.name || "The merchant") }} has nothing for sale.
    </div>

    <ol v-if="stock.length" class="list mt-4">
      <li v-for="entry in stock" :key="entry.key || entry.id" class="inventory-item">
        <span
          v-if="isLastMessage"
          v-interactive="{ target: interactiveItem(entry) }"
          class="interactive"
          :class="[entry.item?.quality]"
        >{{ entry.item?.name || "item" }}</span>
        <span v-else :class="[entry.item?.quality]">{{ entry.item?.name || "item" }}</span>
        for {{ formatPrice(entry.price) }}
      </li>
    </ol>

    <div v-if="walletDisplay" class="wallet-inv color-secondary mt-4">
      You have {{ walletDisplay }}.
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { formatMoney } from "@/core/economy.ts";
import type { Money } from "@/core/economy.ts";
import { capfirst } from "@/core/utils";

const store = useStore();
const props = defineProps<{ message: any }>();

const merchant = computed(() => props.message?.data?.merchant || {});
const stock = computed(() => (
  Array.isArray(props.message?.data?.stock) ? props.message.data.stock : []
));
const isLastMessage = computed(() => (
  store.state.game.last_message[props.message.type] === props.message
));
const economy = computed(() => store.state.game.world?.economy);
const settlementCurrency = computed(() => String(props.message?.data?.funds?.currency || ""));
const settlementBalance = computed(() => (
  store.state.game.player?.economy?.balances?.[settlementCurrency.value] ?? 0
));
const walletDisplay = computed(() => settlementCurrency.value
  ? formatMoney(
    { amount: settlementBalance.value, currency: settlementCurrency.value },
    economy.value,
  )
  : "");

const formatPrice = (price: Money) => formatMoney(price, economy.value);

const interactiveItem = (entry: any) => ({
  ...(entry.item || {}),
  in_container: merchant.value,
  buy_price: entry.price,
  buy_command: `buy ${entry.key} from ${merchant.value.key}`,
  actions: Array.isArray(entry.item?.actions)
    ? [...entry.item.actions, "buy"]
    : { ...(entry.item?.actions || {}), buy: true },
});
</script>
