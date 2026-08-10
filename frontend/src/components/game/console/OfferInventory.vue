<template>
  <div class="inventory indented">
    <template v-if="entries.length">
      <div v-if="isOffer">{{ capfirst(merchant.name || "The merchant") }} will pay:</div>
      <div v-else>{{ capfirst(merchant.name || "The merchant") }} can sell back:</div>
      <ol class="list mb-4">
        <li
          v-for="entry in entries"
          :key="entry.key || entry.id"
          :value="entry.number"
          class="inventory-item"
        >
          <span
            v-if="isLastMessage && isCurrentMerchantRoom && isCurrentEntry(entry)"
            v-interactive="{
              target: interactiveItem(entry),
              primaryAction: true,
              actionContext: isOffer ? 'inventory' : undefined,
            }"
            class="interactive"
            :class="[entry.item?.quality]"
          >{{ entry.item?.name || "item" }}</span>
          <span v-else :class="[entry.item?.quality]">{{ entry.item?.name || "item" }}</span>
          for {{ formatPrice(entry.price) }}
        </li>
      </ol>
      <div v-if="isOffer && props.message?.data?.truncated" class="color-text-50 font-text-light ml-2 mb-2">
        Only the first {{ props.message?.data?.limit }} items are shown.
      </div>
    </template>
    <template v-else>
      <div v-if="isOffer">{{ capfirst(merchant.name || "The merchant") }} does not want anything you are carrying.</div>
      <div v-else>{{ capfirst(merchant.name || "The merchant") }} is not holding any of your recently sold items.</div>
    </template>

    <div v-if="walletDisplay" class="wallet-inv color-secondary">
      You have {{ walletDisplay }}.
    </div>
    <div v-if="isOffer && entries.length && offerHint" class="offer-hint color-text-50 font-text-light ml-2">
      {{ offerHint }}
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
const isOffer = computed(() => Array.isArray(props.message?.data?.offers));
const entries = computed(() => (
  isOffer.value
    ? props.message.data.offers
    : Array.isArray(props.message?.data?.buyback) ? props.message.data.buyback : []
));
const offerHint = computed(() => String(props.message?.data?.hint || ""));
const currentInventoryKeys = computed(() => new Set(
  (store.state.game.player?.inventory || []).map((item: any) => item.key),
));
const isCurrentEntry = (entry: any) => (
  isOffer.value
    ? currentInventoryKeys.value.has(entry.item?.key)
    : !currentInventoryKeys.value.has(entry.item?.key)
);
const isLastMessage = computed(() => (
  store.state.game.last_message[props.message.type] === props.message
));
const isCurrentMerchantRoom = computed(() => (
  merchantProviderIsAvailableInRoom(merchant.value, store.state.game.room)
));
const economy = computed(() => store.state.game.world?.economy);
const settlementCurrency = computed(() => String(
  props.message?.data?.balance?.currency
  || props.message?.data?.funds?.currency
  || entries.value[0]?.price?.currency
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

const buybackCommand = (entry: any): string => {
  const merchantTarget = merchantProviderTarget(merchant.value);
  return merchantTarget
    ? `buyback ${entry.key} from ${merchantTarget}`
    : `buyback ${entry.key}`;
};

const sellCommand = (entry: any): string => {
  const merchantTarget = merchantProviderTarget(merchant.value);
  return merchantTarget
    ? `sell ${entry.key} to ${merchantTarget}`
    : `sell ${entry.key}`;
};

const interactiveItem = (entry: any) => {
  const action = isOffer.value ? "sell" : "buyback";
  return {
    ...(entry.item || {}),
    in_container: isOffer.value ? undefined : merchant.value,
    sell_price: isOffer.value ? entry.price : undefined,
    sell_command: isOffer.value ? sellCommand(entry) : undefined,
    buyback_price: isOffer.value ? undefined : entry.price,
    buyback_command: isOffer.value ? undefined : buybackCommand(entry),
    actions: Array.isArray(entry.item?.actions)
      ? [...entry.item.actions, action]
      : { ...(entry.item?.actions || {}), [action]: true },
  };
};
</script>
