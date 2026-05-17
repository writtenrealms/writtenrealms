<template>
  <div class="stats indented">
    <div
      class="summary"
    >{{ summaryText }}</div>

    <div class="columns">
      <div class="left-side">
        <div class="health stat-entry">
          <div class="label">{{ healthLabel }}</div>
          <div class="value">{{ player.health }} / {{ player.health_max }}</div>
        </div>

        <div v-if="hasEnergy" class="stamina stat-entry">
          <div class="label">{{ energyLabel }}</div>
          <div class="value">{{ energyCurrent }} / {{ energyMax }}</div>
        </div>

        <div class="stamina stat-entry">
          <div class="label">{{ staminaLabel }}</div>
          <div class="value">{{ player.stamina }} / {{ player.stamina_max }}</div>
        </div>

        <div
          v-for="stat in attributeEntries"
          :key="`attribute-${stat.key}`"
          class="stat-entry"
        >
          <div class="label">{{ stat.label }}</div>
          <div class="value">{{ stat.value }}</div>
        </div>

        <div class="stat-entry">
          <div class="label">Gold</div>
          <div class="value">{{ player.gold }}</div>
        </div>

        <div class="stat-entry">
          <div class="label">Medals</div>
          <div class="value">{{ player.medals }}</div>
        </div>

        <div class="stat-entry">
          <div class="label">Exp</div>
          <div class="value">{{ player.experience }} - {{ exp_perc_left }}%</div>
        </div>
      </div>

      <div class="right-side">
        <div
          v-for="stat in derivedEntries"
          :key="`derived-${stat.key}`"
          class="stat-entry"
        >
          <div class="label">{{ stat.label }}</div>
          <div class="value">{{ stat.value }}</div>
        </div>

        <div class="stat-entry">
          <div class="label">Glory</div>
          <div class="value">{{ player.glory }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { capfirst } from "@/core/utils.ts";

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
});

const store = useStore();

const player = computed(() => props.message?.data?.actor || store.state.game.player || {});
const world = computed(() => props.message?.data?.world || store.state.game.world || {});

const resourceLabels = computed(() => world.value?.labels?.resources || {});
const healthLabel = computed(() => resourceLabels.value.health || "Health");
const energyLabel = computed(() => resourceLabels.value.energy || "Energy");
const staminaLabel = computed(() => resourceLabels.value.stamina || "Stamina");
const energyCurrent = computed(() => player.value?.energy ?? 0);
const energyMax = computed(() => player.value?.energy_max ?? 0);
const hasEnergy = computed(() => energyMax.value > 0);

const attributeLabels = computed(() => world.value?.labels?.attributes || {});
const attributeOrder = computed(() => world.value?.labels?.order?.attributes || Object.keys(player.value?.attributes || {}));
const attributeEntries = computed(() => {
  const values = player.value?.attributes || {};
  return attributeOrder.value
    .filter((key: string) => values[key] !== undefined)
    .map((key: string) => ({
      key,
      label: attributeLabels.value[key] || capfirst(key.replace(/_/g, " ")),
      value: values[key],
    }));
});

const derivedLabels = computed(() => world.value?.labels?.derived || {});
const derivedOrder = computed(() => world.value?.labels?.order?.derived || Object.keys(player.value?.derived_stats || {}));
const formatDerivedValue = (key: string, value: number) => {
  const percentMap: Record<string, number | undefined> = {
    armor: player.value?.armor_perc,
    crit: player.value?.crit_perc,
    dodge: player.value?.dodge_perc,
    resilience: player.value?.resilience_perc,
  };
  const perc = percentMap[key];
  if (perc !== undefined && perc !== null) {
    return `${value} - ${perc}%`;
  }
  return `${value}`;
};
const derivedEntries = computed(() => {
  const values = player.value?.derived_stats || {};
  return derivedOrder.value
    .filter((key: string) => values[key] !== undefined)
    .map((key: string) => ({
      key,
      label: derivedLabels.value[key] || capfirst(key.replace(/_/g, " ")),
      value: formatDerivedValue(key, values[key]),
    }));
});

const classLabel = computed(() => {
  const archetype = String(player.value?.archetype || "").trim();
  if (!archetype || world.value?.is_classless) return "";
  const labels = world.value?.labels?.classes || {};
  return labels[archetype] || capfirst(archetype);
});

const summaryText = computed(() => {
  const name = [player.value?.name, player.value?.title]
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join(" ");
  const level = player.value?.level || 1;
  const levelDetails = [`Level ${level}`, core_faction_name.value, classLabel.value]
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join(" ");
  return [name, levelDetails].filter(Boolean).join(" - ");
});

const exp_perc_left = computed(() => {
  const progress = player.value?.experience_progress || 0;
  const needed = player.value?.experience_needed || 0;
  const total = progress + needed;
  if (!total) return 0;
  return Math.round((progress / total) * 100);
});

const core_faction_name = computed(() => {
  const world_factions = world.value?.factions || {};
  const coreCode = player.value?.factions?.core;
  if (!coreCode) return "";
  if (world_factions[coreCode]) {
    return world_factions[coreCode].name;
  }
  return capfirst(coreCode);
});


</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.stats {
  font-size: 14px;

  max-width: 375px;
  display: flex;
  flex-direction: column;

  .columns {
    display: flex;

    .left-side,
    .right-side {
      flex: 1 0;
      display: flex;
      flex-direction: column;

      .stat-entry {
        display: flex;
        justify-content: space-between;

        .label {
          color: $color-text-hex-50;
        }
      }
    }

    .left-side {
      margin-right: 5px;
    }

    .right-side {
      margin-left: 5px;
    }
  }
}
</style>
