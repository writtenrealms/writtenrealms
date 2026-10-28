<template>
  <dl class="dashboard-facts inspection-facts">
    <div v-for="(value, key) in values" :key="key">
      <dt>{{ label(String(key)) }}</dt><dd><slot :name="`value-${key}`" :value="value">{{ display(value) }}</slot></dd>
    </div>
  </dl>
</template>

<script setup lang="ts">
defineProps<{ values: Record<string, unknown> }>();
function label(key: string) { return key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()); }
function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'None';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.length ? value.map(display).join(', ') : 'None';
  if (typeof value === 'object') return Object.entries(value).map(([k, v]) => `${label(k)}: ${display(v)}`).join('\n') || 'None';
  return String(value);
}
</script>

<style scoped lang="scss">
.inspection-facts dd { white-space: pre-wrap; }
</style>
