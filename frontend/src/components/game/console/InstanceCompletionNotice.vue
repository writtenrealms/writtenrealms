<template>
  <ConsoleNotice title="Instance Complete">
    <div>{{ completionText }}</div>
  </ConsoleNotice>
</template>

<script setup lang="ts">
import { computed } from "vue";
import ConsoleNotice from "@/components/game/console/ConsoleNotice.vue";
import { formatClearTime } from "@/services/playerCompletions";

const props = defineProps<{ message: {
  text?: string;
  data?: { template_name?: string; clear_time_ms?: number };
} }>();

const completionText = computed(() => {
  const { template_name, clear_time_ms } = props.message.data ?? {};
  if (template_name && typeof clear_time_ms === "number" && Number.isFinite(clear_time_ms) && clear_time_ms >= 0) {
    return `${template_name} completed in ${formatClearTime(clear_time_ms, 1)}.`;
  }
  return props.message.text;
});
</script>
