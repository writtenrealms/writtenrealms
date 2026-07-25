<template>
  <div class="inventory indented">
    <template v-if="entries.length">
      <div>{{ capfirst(merchant.name || "The merchant") }} can sell back:</div>
      <ol class="list mb-4">
        <li v-for="entry in entries" :key="entry.key || entry.id" class="inventory-item">
          <span
            v-if="isLastMessage"
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
    </template>
    <template v-else>
      <div>{{ capfirst(merchant.name || "The merchant") }} is not holding any of your recently sold items.</div>
    </template>

    <div v-if="walletDisplay" class="wallet-inv color-secondary">
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
const entries = computed(() => (
  Array.isArray(props.message?.data?.buyback) ? props.message.data.buyback : []
));
const isLastMessage = computed(() => (
  store.state.game.last_message[props.message.type] === props.message
));
const economy = computed(() => store.state.game.world?.economy);
const settlementCurrency = computed(() => String(entries.value[0]?.price?.currency || ""));
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
  buyback_price: entry.price,
  buyback_command: `buyback ${entry.key} from ${merchant.value.key}`,
  actions: Array.isArray(entry.item?.actions)
    ? [...entry.item.actions, "buyback"]
    : { ...(entry.item?.actions || {}), buyback: true },
});
</script>
