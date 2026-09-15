<template>
  <ConsoleNotice title="Instance Complete">
    <div>{{ message.text }}</div>
  </ConsoleNotice>
</template>

<script setup lang="ts">
import ConsoleNotice from "@/components/game/console/ConsoleNotice.vue";

defineProps<{ message: { text?: string } }>();
</script>
