<template>
  <ConsoleNotice title="Time Control">
    <div>
      <template v-for="(part, index) in parts" :key="index">
        <code v-if="index % 2 === 1">{{ part.slice(1, -1) }}</code>
        <template v-else>{{ part }}</template>
      </template>
    </div>
  </ConsoleNotice>
</template>

<script setup lang="ts">
import { computed } from "vue";
import ConsoleNotice from "@/components/game/console/ConsoleNotice.vue";

const props = defineProps<{ message: { text?: string } }>();
const parts = computed(() => (props.message.text || "").split(/(`(?:pause|advance|resume)`)/g));
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

code {
  border: 0;
  padding: 0;
  display: inline;
  word-spacing: normal;
  color: $color-secondary;
  font: inherit;
  font-weight: bold;
}
</style>
