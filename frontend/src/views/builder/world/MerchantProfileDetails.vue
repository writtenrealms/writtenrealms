<template>
  <ManifestResourceDetails
    :world-id="worldId"
    :resource-id="profileId"
    resource-label="merchant profile"
    resource-title="Merchant profile"
    list-label="Merchant Profiles"
    expected-kind="merchantprofile"
    response-field="merchant_profile"
    list-route-name="builder_merchant_profile_list"
    detail-route-name="builder_merchant_profile_details"
    detail-id-param="merchant_profile_id"
    :load-resource="loadResource"
    :inherited-world="inheritedWorld"
  >
    <template #header="{ resource: profile }">
      <h2 class="definition-title">{{ profile.name || profile.slug }}</h2>
      <div class="definition-meta color-text-60">
        ID: {{ profile.id }} | Slug: {{ profile.slug }}
      </div>
    </template>

    <template #summary="{ resource: profile }">
      <div class="merchant-summary-grid">
        <div class="summary-block">
          <span class="summary-label">Currency</span>
          <span>{{ currencyLabel(profile) }}</span>
        </div>

        <div class="summary-block">
          <span class="summary-label">Funds</span>
          <span>{{ fundsLabel(profile) }}</span>
        </div>

        <div class="summary-block">
          <span class="summary-label">Pricing</span>
          <span>{{ pricingLabel(profile) }}</span>
        </div>

        <div class="summary-block">
          <span class="summary-label">Restock</span>
          <span>{{ restockLabel(profile.restock_interval_seconds) }}</span>
        </div>

        <div class="summary-block">
          <span class="summary-label">Buyback</span>
          <span>{{ buybackLabel(profile) }}</span>
        </div>
      </div>

      <p v-if="profile.notes" class="profile-notes">
        <span class="summary-label">Notes</span>
        {{ profile.notes }}
      </p>

      <details class="stock-relationships">
        <summary>{{ stockSummary(profile) }}</summary>
        <div v-if="stockSlots(profile).length" class="stock-references">
          <span
            v-for="slot in stockSlots(profile)"
            :key="slot.key"
            class="stock-chip"
          >
            <strong>{{ slot.key }}</strong>
            <span>{{ stockSourceLabel(slot) }} × {{ slot.count }}</span>
            <span class="stock-refresh color-text-60">{{ refreshLabel(slot.refresh) }}</span>
          </span>
        </div>
        <div v-else class="stock-empty color-text-60">No stock slots assigned.</div>
      </details>
    </template>
  </ManifestResourceDetails>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import ManifestResourceDetails from "@/components/builder/world/ManifestResourceDetails.vue";
import {
  fetchMerchantProfile,
  type MerchantStockSlot,
} from "@/services/merchants";

const route = useRoute();
const store = useStore();
const worldId = computed(() => String(route.params.world_id));
const profileId = computed(() => String(route.params.merchant_profile_id));
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const loadResource = () => fetchMerchantProfile(worldId.value, profileId.value);

const stockSlots = (profile: any): MerchantStockSlot[] => (
  Array.isArray(profile.stock) ? profile.stock : []
);

const stockCount = (profile: any): number => {
  const slots = stockSlots(profile);
  if (slots.length || Array.isArray(profile.stock)) return slots.length;
  const count = Number(profile.stock_count);
  return Number.isFinite(count) ? count : 0;
};

const stockSummary = (profile: any): string => {
  const count = stockCount(profile);
  return `${count} stock ${count === 1 ? "slot" : "slots"}`;
};

const currencyLabel = (profile: any): string => {
  const currency = String(profile.settlement_currency || "").trim();
  return currency ? currency.toUpperCase() : "Unset";
};

const formatAmount = (value: unknown): string => {
  const amount = Number(value);
  return Number.isFinite(amount) ? new Intl.NumberFormat().format(amount) : "0";
};

const fundsLabel = (profile: any): string => {
  if (profile.funds_mode !== "finite") return "Unlimited";
  return `${formatAmount(profile.purchase_budget)} ${currencyLabel(profile)}`;
};

const pricingLabel = (profile: any): string => (
  `${profile.sell_markup ?? 1}× sell · ${profile.buy_multiplier ?? 0}× buy`
);

const restockLabel = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "Manual";
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return String(value);
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
};

const buybackLabel = (profile: any): string => {
  if (!profile.buyback_enabled) return "Disabled";
  const maxItems = Number(profile.buyback_max_items) || 0;
  return `Enabled · ${maxItems} ${maxItems === 1 ? "item" : "items"}`;
};

const referenceName = (reference: unknown): string => String(reference || "")
  .replace(/^[^.]+\./, "")
  .replace(/[-_]/g, " ")
  .replace(/\b\w/g, (letter) => letter.toUpperCase());

const stockSourceLabel = (slot: MerchantStockSlot): string => {
  if (slot.item_bundle) return `Bundle: ${referenceName(slot.item_bundle)}`;
  return referenceName(slot.item_definition) || "Unknown item";
};

const refreshLabel = (refresh: MerchantStockSlot["refresh"]): string => (
  refresh === "reroll_on_restock" ? "Reroll on restock" : "Fill missing"
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.definition-title {
  margin-bottom: 0.35rem;
}

.definition-meta {
  min-width: 0;
  overflow-wrap: anywhere;
}

.merchant-summary-grid {
  display: grid;
  gap: 0.75rem 1.5rem;
  grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
  margin-top: 0.75rem;
}

.summary-block {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  min-width: 0;
}

.summary-label {
  color: $color-text-hex-60;
  display: block;
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.profile-notes {
  line-height: 1.45;
  margin: 0.85rem 0 0;
  max-width: 62rem;

  .summary-label {
    margin-bottom: 0.3rem;
  }
}

.stock-relationships {
  margin-top: 0.85rem;
  min-width: 0;

  summary {
    cursor: pointer;
  }
}

.stock-references {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.5rem;
}

.stock-chip {
  border: 1px solid $color-form-border;
  display: inline-flex;
  flex-wrap: wrap;
  gap: 0.3rem 0.55rem;
  max-width: 100%;
  overflow-wrap: anywhere;
  padding: 0.3rem 0.45rem;
}

.stock-refresh {
  white-space: nowrap;
}

.stock-empty {
  margin-top: 0.5rem;
}
</style>
