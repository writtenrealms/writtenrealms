<template>
  <ModalView>
    <div class="power-analysis">
      <header class="power-analysis-header">
        <div>
          <h2>Power Analysis</h2>
          <div class="entity-name">{{ analysis.entity?.name || analysis.entity?.slug }}</div>
        </div>
        <div class="score-block">
          <div class="score-value">{{ formatNumber(analysis.summary?.budget_score) }}</div>
          <div class="score-label">score</div>
        </div>
      </header>

      <section class="summary-grid">
        <div v-for="row in summaryRows" :key="row.label" class="summary-cell">
          <div class="summary-label">{{ row.label }}</div>
          <div class="summary-value">{{ row.value }}</div>
        </div>
      </section>

      <section class="analysis-section">
        <h3>Categories</h3>
        <div class="category-grid">
          <div v-for="category in analysis.categories || []" :key="category.key" class="category-row">
            <div>{{ category.label }}</div>
            <div>{{ formatNumber(category.score) }}</div>
          </div>
        </div>
      </section>

      <section class="analysis-section">
        <h3>Metrics</h3>
        <div class="metrics-grid">
          <div v-for="metric in metricRows" :key="metric.label" class="metric-row">
            <div>{{ metric.label }}</div>
            <div>{{ metric.value }}</div>
          </div>
        </div>
      </section>

      <section class="analysis-section">
        <h3>Drivers</h3>
        <table class="driver-table">
          <thead>
            <tr>
              <th>Stat</th>
              <th>Category</th>
              <th>Value</th>
              <th>Score</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="driver in analysis.drivers || []" :key="`${driver.category}-${driver.stat}`">
              <td>
                <div>{{ driver.label }}</div>
                <div v-if="driver.detail" class="driver-detail">{{ driver.detail }}</div>
              </td>
              <td>{{ categoryLabel(driver.category) }}</td>
              <td>{{ formatNumber(driver.value) }}</td>
              <td>{{ formatNumber(driver.score) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="analysis.diagnostics?.length" class="analysis-section">
        <h3>Notes</h3>
        <ul class="diagnostics">
          <li v-for="diagnostic in analysis.diagnostics" :key="diagnostic">{{ diagnostic }}</li>
        </ul>
      </section>
    </div>
  </ModalView>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import ModalView from "@/components/ui/ModalView.vue";

type PowerAnalysis = {
  kind: string;
  entity?: Record<string, any>;
  summary?: Record<string, any>;
  categories?: Array<Record<string, any>>;
  drivers?: Array<Record<string, any>>;
  metrics?: Record<string, any>;
  diagnostics?: string[];
};

const props = defineProps<{
  analysis: PowerAnalysis;
}>();

const categoryLabels: Record<string, string> = {
  offense: "Offense",
  defense: "Defense",
  sustain: "Sustain",
  utility: "Utility",
};

const titleCase = (value: string): string => {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const formatNumber = (value: unknown): string => {
  const numberValue = Number(value || 0);
  if (!Number.isFinite(numberValue)) return "0";
  if (Math.abs(numberValue) >= 100) {
    return numberValue.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  return numberValue.toLocaleString(undefined, { maximumFractionDigits: 2 });
};

const formatValue = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return formatNumber(value);
  return String(value);
};

const categoryLabel = (category: string): string => categoryLabels[category] || titleCase(category);

const summaryRows = computed(() => {
  const summary = props.analysis.summary || {};
  const rows = [
    ["Level", summary.level],
    ["Power Level", summary.estimated_power_level],
    ["Reference", summary.reference_score],
    ["Ratio", summary.reference_ratio ? `${formatNumber(summary.reference_ratio)}x` : ""],
  ];
  if (summary.equipment_type) rows.splice(1, 0, ["Slot", summary.equipment_type]);
  if (summary.armor_class) rows.splice(2, 0, ["Armor Class", summary.armor_class]);
  if (summary.type) rows.splice(1, 0, ["Type", summary.type]);
  return rows.map(([label, value]) => ({
    label,
    value: formatValue(value),
  }));
});

const metricRows = computed(() => {
  const metrics = props.analysis.metrics || {};
  return Object.keys(metrics).map((key) => ({
    label: titleCase(key),
    value: formatValue(metrics[key]),
  }));
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.power-analysis {
  min-width: min(720px, calc(100vw - 48px));
  max-width: 820px;
}

.power-analysis-header {
  align-items: flex-start;
  border-bottom: 1px solid $color-background-light-border;
  display: flex;
  gap: 20px;
  justify-content: space-between;
  margin-bottom: 18px;
  padding-bottom: 14px;

  h2 {
    margin: 0 0 4px;
  }
}

.entity-name,
.summary-label,
.score-label,
.driver-detail {
  color: $color-text-hex-60;
}

.score-block {
  min-width: 96px;
  text-align: right;
}

.score-value {
  color: $color-secondary;
  font-size: 28px;
  line-height: 32px;
}

.summary-grid,
.metrics-grid,
.category-grid {
  display: grid;
  gap: 8px;
}

.summary-grid {
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  margin-bottom: 18px;
}

.summary-cell,
.metric-row,
.category-row {
  background: $color-background-light;
  border: 1px solid $color-background-light-border;
  min-width: 0;
  padding: 8px 10px;
}

.summary-value {
  overflow-wrap: anywhere;
}

.analysis-section {
  margin-top: 18px;
  overflow-x: auto;

  h3 {
    color: $color-secondary;
    font-size: 14px;
    margin: 0 0 8px;
    text-transform: uppercase;
  }
}

.metrics-grid,
.category-grid {
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
}

.metric-row,
.category-row {
  display: flex;
  gap: 12px;
  justify-content: space-between;
}

.driver-table {
  border-collapse: collapse;
  width: 100%;

  th,
  td {
    border-bottom: 1px solid $color-background-light-border;
    padding: 8px;
    text-align: left;
    vertical-align: top;
  }

  th:last-child,
  td:last-child,
  th:nth-child(3),
  td:nth-child(3) {
    text-align: right;
  }
}

.diagnostics {
  margin: 0;
  padding-left: 18px;

  li {
    margin-bottom: 6px;
  }
}
</style>
