<template>
  <details class="json-inspector" :open="open">
    <summary>{{ title }}</summary>
    <div class="json-inspector-body">
      <div class="dashboard-actions">
        <button type="button" class="btn-small button-gray" @click="copy"><Copy :size="13" aria-hidden="true" /> COPY JSON</button>
        <span role="status" class="dashboard-note">{{ copyStatus }}</span>
      </div>
      <pre tabindex="0" :aria-label="title">{{ formatted }}</pre>
    </div>
  </details>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Copy } from '@lucide/vue';
const props = defineProps<{ title: string; value: unknown; open?: boolean }>();
const formatted = computed(() => JSON.stringify(props.value ?? null, null, 2));
const copyStatus = ref('');
watch(formatted, () => { copyStatus.value = ''; });
async function copy() {
  try { await navigator.clipboard.writeText(formatted.value); copyStatus.value = 'Copied'; }
  catch { copyStatus.value = 'Copy unavailable. Select the JSON below to copy it.'; }
}
</script>

<style lang="scss">
@import '@/styles/colors.scss';
.json-inspector {
  min-width: 0;
  border: 1px solid $color-background-border;
  background: $color-background-light;
  summary { cursor: pointer; padding: 1rem; color: $color-secondary; overflow-wrap: anywhere; }
  summary:focus-visible { outline: 2px solid $color-secondary; outline-offset: -2px; }
  .json-inspector-body { padding: 0 1rem 1rem; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; max-height: 38rem; font-size: 12px; line-height: 1.6; margin: 1rem 0 0; }
}
</style>
