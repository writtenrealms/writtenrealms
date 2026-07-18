<template>
  <div class="recipe-details indented">
    <div :class="[recipe.output?.quality]">{{ recipe.name || recipe.output?.name || "Recipe" }}</div>
    <div v-if="equipmentLabel" class="color-text-60">{{ equipmentLabel }}</div>

    <div v-if="outputStats.length" class="requirements mt-4">
      <div v-for="stat in outputStats" :key="stat.label">
        {{ stat.label }}: {{ stat.value }}
      </div>
    </div>

    <div class="requirements mt-4">
      <div v-if="costDisplay">
        Crafting fee: {{ costDisplay }}
        <span v-if="currencyOwned != null" class="color-text-60">
          (you have {{ ownedCurrencyDisplay }})
        </span>
      </div>
      <div v-for="input in recipe.inputs || []" :key="input.material?.key || input.material?.slug">
        {{ input.material?.name || "Material" }}: {{ input.owned || 0 }} / {{ input.required || 0 }}
      </div>
    </div>

    <div v-if="!recipe.conditions_met" class="color-text-60 mt-4">
      {{ recipe.failure_message || "You do not meet this recipe's requirements." }}
    </div>
    <div v-else-if="recipe.ready" class="color-secondary mt-4">Ready to craft.</div>
    <div v-else-if="missingRequirements.length" class="color-text-60 mt-4">
      Missing: {{ joinPhrases(missingRequirements) }}.
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { formatMoney } from "@/core/economy.ts";
import type { Money } from "@/core/economy.ts";

const store = useStore();
const props = defineProps<{ message: any }>();
const recipe = computed(() => props.message?.data?.recipe || {});
const economy = computed(() => store.state.game.world?.economy);
const cost = computed<Money | null>(() => recipe.value.cost || null);
const currencyOwned = computed<number | null>(() => {
  const value = recipe.value.currency_owned;
  return value == null ? null : Number(value);
});
const currencyMissing = computed(() => Number(
  recipe.value.currency_missing ?? recipe.value.missing_currency ?? 0,
));

const costDisplay = computed(() => (
  cost.value ? formatMoney(cost.value, economy.value) : ""
));
const ownedCurrencyDisplay = computed(() => (
  cost.value && currencyOwned.value != null
    ? formatMoney(
      { amount: currencyOwned.value, currency: cost.value.currency },
      economy.value,
    )
    : ""
));
const equipmentLabel = computed(() => {
  const output = recipe.value.output || {};
  const type = String(output.equipment_type || "").replace(/_/g, " ");
  return [output.armor_class, type]
    .filter(Boolean)
    .join(" ")
    .replace(/^./, (letter) => letter.toUpperCase());
});

const rangeText = (range: any) => {
  const minimum = Number(range?.min ?? 0);
  const maximum = Number(range?.max ?? minimum);
  return minimum === maximum ? String(minimum) : `${minimum}-${maximum}`;
};
const statLabel = (name: string) => name
  .replace(/_/g, " ")
  .replace(/\b\w/g, (letter) => letter.toUpperCase());
const outputStats = computed(() => {
  const output = recipe.value.output || {};
  const stats: Array<{ label: string; value: string | number }> = [];
  if (Number(output.armor || 0) !== 0) stats.push({ label: "Armor", value: output.armor });
  if (Number(output.weapon_damage || 0) !== 0) {
    stats.push({ label: "Weapon Damage", value: output.weapon_damage });
  }
  for (const [name, range] of Object.entries(output.attributes || {})) {
    stats.push({ label: statLabel(name), value: rangeText(range) });
  }
  return stats;
});

const missingRequirements = computed(() => {
  const missing = (recipe.value.inputs || [])
    .filter((entry: any) => Number(entry.missing || 0) > 0)
    .map((entry: any) => `${entry.missing} ${entry.material?.name || "material"}`);
  if (currencyMissing.value > 0 && cost.value) {
    missing.push(formatMoney(
      { amount: currencyMissing.value, currency: cost.value.currency },
      economy.value,
    ));
  }
  return missing;
});

const joinPhrases = (phrases: string[]) => {
  if (phrases.length <= 1) return phrases[0] || "requirements";
  if (phrases.length === 2) return `${phrases[0]} and ${phrases[1]}`;
  return `${phrases.slice(0, -1).join(", ")}, and ${phrases[phrases.length - 1]}`;
};
</script>

<style lang="scss" scoped>
.requirements {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
</style>
