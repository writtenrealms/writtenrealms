<template>
  <div v-if="summary.long" class="yaml-text-value">
    <div v-if="!open || excerpt.matched" class="yaml-text-preview">{{ excerpt.text }}</div>
    <div class="yaml-text-controls">
      <button type="button" class="btn-thin" :aria-expanded="open"
        :aria-label="`${open ? 'Collapse value for' : 'Show full value for'} ${label}`" @click="open = !open">
        {{ open ? 'COLLAPSE VALUE' : 'SHOW FULL VALUE' }}
      </button>
      <span class="yaml-text-summary">{{ summary.lines > 1 ? `${summary.lines.toLocaleString()} lines` : `${summary.characters.toLocaleString()} characters` }}</span>
      <span v-if="excerpt.matched" class="yaml-text-summary">Matching excerpt</span>
      <span class="yaml-viewer-type">{{ typeLabel }}</span>
    </div>
    <div v-if="open" class="yaml-text-full" tabindex="0" role="region" :aria-label="`${label}: full value`">{{ value }}</div>
  </div>
  <template v-else>{{ value }}<span class="yaml-viewer-type">{{ typeLabel }}</span></template>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { describeYamlText, yamlTextExcerpt } from '@/core/yamlTextPreview';

const props = defineProps<{ value: string; type: string; label: string; query: string }>();
const open = ref(false);
const summary = computed(() => describeYamlText(props.value));
const excerpt = computed(() => yamlTextExcerpt(props.value, props.query));
const typeLabel = computed(() => props.type === 'alias' ? 'reference' : props.type);
watch(() => props.value, () => { open.value = false; });
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';

.yaml-viewer-type { margin-left: 0.75rem; color: $color-text-hex-70; font-size: 0.65rem; border: 1px solid $color-form-border; border-radius: 3px; padding: 0.05rem 0.25rem; white-space: nowrap; }
.yaml-text-preview { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
.yaml-text-controls {
  display: flex; align-items: center; flex-wrap: wrap; gap: 0.35rem 0.75rem;
  margin-top: 0.4rem; white-space: normal;
  .yaml-viewer-type { margin-left: 0; }
}
.yaml-text-summary { color: $color-text-hex-70; font-size: 0.75rem; }
.yaml-text-full {
  box-sizing: border-box;
  max-height: min(16rem, 35dvh);
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border: 1px solid $color-form-border;
  background: $color-background-black;
  padding: 0.6rem;
  margin-top: 0.5rem;
}
</style>
