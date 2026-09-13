<template>
  <div class="console-notice">
    <div class="console-notice-title mb-4">{{ title }}</div>
    <slot />
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string }>();
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.console-notice {
  border: 1px solid $color-background-light-border;
  padding: 1rem;
}

.console-notice-title {
  color: $color-primary;
  font-weight: bold;
}
</style>
