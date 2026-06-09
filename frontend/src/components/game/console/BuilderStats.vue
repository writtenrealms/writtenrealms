<template>
  <div class="builder-stats indented">
    <div class="summary">{{ summaryText }}</div>

    <div class="columns">
      <div class="left-side">
        <div
          v-for="detail in detailEntries"
          :key="`detail-${detail.key}`"
          class="stat-entry"
        >
          <div class="label">{{ detail.label }}</div>
          <div class="value">{{ detail.value }}</div>
        </div>

        <div
          v-for="resource in resourceEntries"
          :key="`resource-${resource.key}`"
          class="stat-entry"
        >
          <div class="label">{{ resource.label }}</div>
          <div class="value">{{ resource.value }}</div>
        </div>
      </div>

      <div class="right-side">
        <div v-if="attributeEntries.length" class="section">
          <div class="section-title">Attributes</div>
          <div
            v-for="stat in attributeEntries"
            :key="`attribute-${stat.key}`"
            class="stat-entry section-entry"
          >
            <div class="label">{{ stat.label }}</div>
            <div class="value">{{ stat.value }}</div>
          </div>
        </div>

        <div
          v-for="stat in statEntries"
          :key="`stat-${stat.key}`"
          class="stat-entry"
        >
          <div class="label">{{ stat.label }}</div>
          <div class="value">{{ stat.value }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { capfirst, formatCombatStatValue } from "@/core/utils.ts";

const props = defineProps<{
  message: any;
}>();

const store = useStore();

const target = computed(() => props.message?.data?.target || {});
const targetType = computed(() => props.message?.data?.target_type || target.value?.char_type || "");
const world = computed(() => props.message?.data?.world || store.state.game.world || {});

const titleCase = (value: string) => capfirst(String(value || "").replace(/_/g, " "));

const resourceLabels = computed(() => world.value?.labels?.resources || {});
const labelForResource = (key: string) => resourceLabels.value[key] || titleCase(key);
const statLabels = computed(() => world.value?.labels?.stats || {});

const labelForRegen = (key: string) => {
  const statKey = `${key}_regen`;
  return statLabels.value[statKey] || `${labelForResource(key)} Regen`;
};

const classLabel = computed(() => {
  const archetype = String(target.value?.archetype || "").trim();
  if (!archetype || world.value?.is_classless) return "";
  const labels = world.value?.labels?.classes || {};
  return labels[archetype] || titleCase(archetype);
});

const summaryText = computed(() => {
  const name = [target.value?.name, target.value?.title]
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join(" ");
  const key = target.value?.key ? `(${target.value.key})` : "";
  const level = target.value?.level || 1;
  const details = [`Level ${level}`, classLabel.value]
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join(" ");
  return [[name, key].filter(Boolean).join(" "), details].filter(Boolean).join(" - ");
});

const formatChoice = (value: any) => titleCase(String(value || "").replace(/:/g, ": "));

const detailEntries = computed(() => {
  const entries: any[] = [
    { key: "type", label: "Type", value: targetType.value || "character" },
  ];
  if (targetType.value === "mob" && target.value?.aggression) {
    entries.push({
      key: "aggression",
      label: "Aggression",
      value: formatChoice(target.value.aggression),
    });
  }
  if (targetType.value === "mob") {
    entries.push({
      key: "exp_worth",
      label: "Exp Worth",
      value: target.value?.exp_worth ?? 0,
    });
  } else {
    entries.push({
      key: "experience",
      label: "Exp",
      value: target.value?.experience ?? 0,
    });
  }
  entries.push({
    key: "gold",
    label: "Gold",
    value: target.value?.gold ?? 0,
  });
  if (targetType.value === "player") {
    entries.push({
      key: "glory",
      label: "Glory",
      value: target.value?.glory ?? 0,
    });
    entries.push({
      key: "medals",
      label: "Medals",
      value: target.value?.medals ?? 0,
    });
  }
  return entries;
});

const resourceEntries = computed(() => {
  return [
    ["health", "health_max", "health_regen"],
    ["energy", "energy_max", "energy_regen"],
    ["stamina", "stamina_max", "stamina_regen"],
  ]
    .filter(([currentKey, maxKey, regenKey]) =>
      target.value?.[currentKey] !== undefined ||
      target.value?.[maxKey] !== undefined ||
      target.value?.[regenKey] !== undefined
    )
    .flatMap(([currentKey, maxKey, regenKey]) => {
      const current = target.value?.[currentKey] ?? 0;
      const max = target.value?.[maxKey] ?? 0;
      const regen = target.value?.[regenKey];
      const entries = [
        {
          key: currentKey,
          label: labelForResource(currentKey),
          value: `${current} / ${max}`,
        },
      ];
      if (regen !== undefined && regen !== null) {
        entries.push({
          key: regenKey,
          label: labelForRegen(currentKey),
          value: regen,
        });
      }
      return entries;
    });
});

const attributeLabels = computed(() => world.value?.labels?.attributes || {});
const attributeOrder = computed(() => world.value?.labels?.order?.attributes || Object.keys(target.value?.attributes || {}));
const attributeEntries = computed(() => {
  const values = target.value?.attributes || {};
  return attributeOrder.value
    .filter((key: string) => values[key] !== undefined)
    .map((key: string) => ({
      key,
      label: attributeLabels.value[key] || titleCase(key),
      value: values[key],
    }));
});

const statOrder = computed(() => world.value?.labels?.order?.stats || Object.keys(target.value?.stats || {}));
const statEntries = computed(() => {
  const values = target.value?.stats || {};
  const resourceRegenKeys = new Set(["health_regen", "energy_regen", "stamina_regen"]);
  return statOrder.value
    .filter((key: string) => values[key] !== undefined)
    .filter((key: string) => !resourceRegenKeys.has(key))
    .map((key: string) => ({
      key,
      label: statLabels.value[key] || titleCase(key),
      value: formatCombatStatValue(world.value, target.value, key, values[key]),
    }));
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.builder-stats {
  font-size: 14px;
  max-width: 430px;
  display: flex;
  flex-direction: column;

  .summary {
    margin-bottom: 4px;
  }

  .columns {
    display: flex;
  }

  .left-side,
  .right-side {
    flex: 1 0;
    display: flex;
    flex-direction: column;
  }

  .left-side {
    margin-right: 8px;
  }

  .right-side {
    margin-left: 8px;
  }

  .section + .section {
    margin-top: 8px;
  }

  .section-title {
    color: $color-text-hex-50;
    margin-bottom: 2px;
  }

  .section-entry {
    padding-left: 10px;
  }

  .stat-entry {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }

  .label {
    color: $color-text-hex-50;
  }

  .value {
    text-align: right;
    white-space: nowrap;
  }
}
</style>
