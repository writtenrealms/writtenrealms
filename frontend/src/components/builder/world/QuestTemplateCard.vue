<template>
  <component
    :is="componentTag"
    class="panel panel-shadow quest-template-card"
    v-bind="componentProps"
  >
    <div class="quest-card-content p-2">
      <div class="quest-header mt-2 mb-4">
        <h3 class="color-text mb-1">
          {{ quest.name.toUpperCase() }}
          <span class="font-text-light color-text-50 ml-2">[ {{ quest.slug }} ]</span>
        </h3>
      </div>

      <div class="quest-meta my-2 color-text-70">
        {{ quest.status }} {{ quest.quest_type }} &mdash; scope: {{ quest.scope }}
      </div>

      <div class="quest-summary color-text-70">
        {{ summary }}
      </div>
    </div>
  </component>
</template>

<script lang="ts" setup>
import { computed } from "vue";

const props = defineProps<{
  quest: any;
  to?: Record<string, any> | null;
}>();

const componentTag = computed(() => (props.to ? "router-link" : "div"));

const componentProps = computed(() => (props.to ? { to: props.to } : {}));

const stepCount = computed(() => {
  if (Array.isArray(props.quest?.graph?.steps)) {
    return props.quest.graph.steps.length;
  }
  if (Array.isArray(props.quest?.manifest?.spec?.steps)) {
    return props.quest.manifest.spec.steps.length;
  }
  return 0;
});

const sourceTypes = computed(() => {
  if (!Array.isArray(props.quest?.discovery_policy?.sources)) {
    return [];
  }
  return props.quest.discovery_policy.sources.map((source: any) => source.type).filter(Boolean);
});

const summary = computed(() => {
  const summaryParts = [`${stepCount.value} step${stepCount.value === 1 ? "" : "s"}`];
  if (sourceTypes.value.length) {
    summaryParts.push(`sources: ${sourceTypes.value.join(", ")}`);
  } else {
    summaryParts.push("no discovery sources configured");
  }
  if (props.quest?.arc?.name) {
    summaryParts.push(`arc: ${props.quest.arc.name}`);
  }
  return summaryParts.join(" • ");
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.quest-template-card {
  display: block;
  text-decoration: none;
  color: inherit;

  &[href] {
    cursor: pointer;
    transition:
      border-color 140ms ease,
      box-shadow 140ms ease,
      transform 140ms ease,
      background-color 140ms ease;

    &:hover,
    &:focus-visible {
      text-decoration: none;
      border-color: rgba($color-primary, 0.5);
      box-shadow: 0 0 0 1px rgba($color-primary, 0.18);
      background: rgba($color-primary, 0.04);
    }
  }
}
</style>
