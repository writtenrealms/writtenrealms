<template>
  <Teleport to="body">
    <dialog ref="dialog" class="yaml-viewer" aria-labelledby="yaml-viewer-title" aria-describedby="yaml-viewer-description"
      @cancel.prevent="emit('close')" @keydown.esc.stop
      @pointerdown="backdropPointerDown = isOutsideDialog($event)" @click="onBackdropClick">
      <header class="yaml-viewer-header">
        <div>
          <h2 id="yaml-viewer-title">{{ title }}</h2>
          <p id="yaml-viewer-description">Read-only preview. Press Esc or close to return to the editor.</p>
          <span v-if="hasChanges" class="yaml-viewer-draft">Includes unsaved changes</span>
        </div>
        <button type="button" class="icon-button" aria-label="Close YAML viewer" title="Close (Esc)" @click="emit('close')"><X aria-hidden="true" /></button>
      </header>

      <div class="yaml-viewer-toolbar">
        <div class="yaml-viewer-modes" role="group" aria-label="View mode">
          <button type="button" class="btn-small" :aria-pressed="mode === 'builder'" :disabled="!!model.error" @click="mode = 'builder'">BUILDER VIEW</button>
          <button type="button" class="btn-small" :aria-pressed="mode === 'source'" @click="mode = 'source'">YAML SOURCE</button>
        </div>
        <div class="yaml-viewer-tools">
          <template v-if="mode === 'builder'">
            <button type="button" class="btn-thin" :disabled="loading || !!query.trim()" @click="expandAll">EXPAND ALL</button>
            <button type="button" class="btn-thin" :disabled="loading || !!query.trim()" @click="expanded = new Set()">COLLAPSE ALL</button>
          </template>
          <button type="button" class="btn-small" @click="copySource">COPY YAML</button>
          <span class="yaml-viewer-copy-status" role="status">{{ copyStatus }}</span>
        </div>
      </div>

      <div v-if="mode === 'builder'" class="yaml-viewer-search form-group">
        <label for="yaml-viewer-search"><Search aria-hidden="true" /> Find a field or value</label>
        <input id="yaml-viewer-search" v-model="searchInput" type="search" placeholder="Try death routing, room@10, or a name…" autocomplete="off" />
        <span class="yaml-viewer-hint">Matching sections open automatically.</span>
      </div>

      <p v-if="model.error" class="yaml-viewer-error" role="status">Builder view is unavailable: {{ model.error }}</p>
      <div v-if="loading && mode === 'builder'" class="yaml-viewer-content" role="status">Preparing builder view…</div>
      <div v-else-if="mode === 'builder'" ref="treeContent" class="yaml-viewer-content" tabindex="0" aria-label="Manifest fields">
        <p v-if="!rows.length" class="yaml-viewer-hint">{{ query ? 'No fields or values match your search.' : 'This YAML document is empty.' }}</p>
        <div v-for="node in displayedRows" :key="node.id" class="yaml-viewer-row"
          :class="{ 'yaml-viewer-section': node.count, 'yaml-viewer-document': node.parent < 0 }"
          :style="{ '--yaml-depth': Math.min(node.depth, 6) }">
          <div class="yaml-viewer-field">
            <button v-if="node.count" type="button" class="yaml-viewer-disclosure" :aria-expanded="isExpanded(node.id)"
              :disabled="!!query.trim()" @click="toggle(node.id)">
              <ChevronDown v-if="isExpanded(node.id)" aria-hidden="true" /><ChevronRight v-else aria-hidden="true" />
              <span>{{ node.label }}</span>
            </button>
            <span v-else class="yaml-viewer-label">{{ node.label }}</span>
            <span v-if="node.parent >= 0" class="yaml-viewer-key">{{ node.key }}</span>
          </div>
          <div class="yaml-viewer-value" :class="`yaml-color-${node.type}`">
            <template v-if="node.type === 'map' || node.type === 'list'">
              {{ node.count ? `${node.count} ${node.type === 'map' ? (node.count === 1 ? 'field' : 'fields') : (node.count === 1 ? 'item' : 'items')}` : (node.type === 'map' ? 'Empty map {}' : 'Empty list []') }}
            </template>
            <YamlScalarValue v-else :value="node.value" :type="node.type" :label="node.label" :query="query" />
            <span v-if="node.note" class="yaml-viewer-note">{{ node.note }}</span>
          </div>
          <span class="yaml-viewer-line" :title="`Source line ${node.line}`">L{{ node.line }}</span>
        </div>
        <button v-if="rows.length > rowLimit" type="button" class="btn-small yaml-viewer-more" @click="rowLimit += 300">SHOW NEXT {{ Math.min(300, rows.length - rowLimit) }} FIELDS</button>
      </div>
      <div v-else class="yaml-viewer-content yaml-viewer-source">
        <pre v-if="highlightSource" tabindex="0" aria-label="YAML source"><code><span v-for="(segment, index) in sourceSegments" :key="index" :class="`yaml-color-${segment.type}`">{{ segment.text }}</span></code></pre>
        <textarea v-else :value="source" readonly spellcheck="false" aria-label="YAML source" />
      </div>

      <footer class="yaml-viewer-footer">
        <span>{{ model.documents }} {{ model.documents === 1 ? 'document' : 'documents' }}<template v-if="mode === 'builder'"> · {{ rows.length }} visible fields</template></span>
        <span>Read-only preview · Copy keeps the original YAML</span>
      </footer>
    </dialog>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { ChevronDown, ChevronRight, Search, X } from '@lucide/vue';
import { visibleYamlRows, yamlSourceSegments, type YamlViewModel } from '@/core/yamlViewer';
import YamlScalarValue from './YamlScalarValue.vue';

const props = withDefaults(defineProps<{ source: string; title?: string; hasChanges?: boolean }>(), {
  title: 'YAML viewer', hasChanges: false,
});
const emit = defineEmits<{ (event: 'close'): void }>();
const dialog = ref<HTMLDialogElement>();
const treeContent = ref<HTMLElement>();
const mode = ref<'builder' | 'source'>('builder');
const model = shallowRef<YamlViewModel>({ nodes: [], tokens: [], documents: 0, error: '' });
const expanded = ref(new Set<number>());
const loading = ref(true);
const searchInput = ref('');
const query = ref('');
const rowLimit = ref(300);
const copyStatus = ref('');
let worker: Worker | undefined;
let parseTimeout: ReturnType<typeof setTimeout> | undefined;
let searchTimeout: ReturnType<typeof setTimeout> | undefined;
let previousOverflow = '';
let backdropPointerDown = false;

const isOutsideDialog = (event: MouseEvent) => {
  const bounds = dialog.value?.getBoundingClientRect();
  return !!bounds && (event.clientX < bounds.left || event.clientX > bounds.right
    || event.clientY < bounds.top || event.clientY > bounds.bottom);
};
const onBackdropClick = (event: MouseEvent) => {
  // A selection dragged out of the source should not dismiss the preview.
  if (backdropPointerDown && isOutsideDialog(event)) emit('close');
  backdropPointerDown = false;
};

const rows = computed(() => visibleYamlRows(model.value.nodes, expanded.value, query.value));
const displayedRows = computed(() => rows.value.slice(0, rowLimit.value));
// Avoid creating thousands of span elements for an entire exported world.
const highlightSource = computed(() => !model.value.error && !loading.value && props.source.length < 200_000 && model.value.tokens.length < 5_000);
const sourceSegments = computed(() => mode.value === 'source' && highlightSource.value ? yamlSourceSegments(props.source, model.value.tokens) : []);
const isExpanded = (id: number) => !!query.value.trim() || expanded.value.has(id);
const toggle = (id: number) => expanded.value.has(id) ? expanded.value.delete(id) : expanded.value.add(id);
const expandAll = () => { expanded.value = new Set(model.value.nodes.filter(node => node.count).map(node => node.id)); };

const copySource = async () => {
  try {
    await navigator.clipboard.writeText(props.source);
    copyStatus.value = 'Copied.';
  } catch {
    copyStatus.value = 'Copy failed. Select and copy the YAML in source view.';
  }
};

const parseSource = () => {
  worker?.terminate();
  clearTimeout(parseTimeout);
  loading.value = true;
  model.value = { nodes: [], tokens: [], documents: 0, error: '' };
  copyStatus.value = '';
  const fail = (message: string) => {
    worker?.terminate();
    clearTimeout(parseTimeout);
    loading.value = false;
    model.value = { nodes: [], tokens: [], documents: 0, error: message };
    mode.value = 'source';
  };
  try {
    worker = new Worker(new URL('../../../core/yamlViewer.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (event: MessageEvent<YamlViewModel>) => {
      model.value = event.data;
      expanded.value = new Set(event.data.nodes.filter(node => node.depth < 2 && node.count).map(node => node.id));
      loading.value = false;
      if (event.data.error) mode.value = 'source';
      clearTimeout(parseTimeout);
      worker?.terminate();
    };
    worker.onerror = () => fail('Could not prepare the viewer. You can still read and copy the full source.');
    worker.postMessage(props.source);
    parseTimeout = setTimeout(() => fail('Preparing this document took too long. The full source is still available.'), 10_000);
  } catch {
    fail('Could not start the viewer. You can still read and copy the full source.');
  }
};

watch(searchInput, value => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => { query.value = value; }, 150);
});
watch([query, expanded, () => props.source], () => {
  rowLimit.value = 300;
  if (treeContent.value) treeContent.value.scrollTop = 0;
});
watch(() => props.source, parseSource);
onMounted(() => {
  previousOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  dialog.value?.showModal();
  parseSource();
});
onBeforeUnmount(() => {
  worker?.terminate();
  clearTimeout(parseTimeout);
  clearTimeout(searchTimeout);
  dialog.value?.close();
  document.body.style.overflow = previousOverflow;
});
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';
@import '@/styles/fonts.scss';
@import '@/styles/layout.scss';

.yaml-viewer {
  --yaml-modal-gap: clamp(1.5rem, 3vw, 3rem);
  background: $color-background;
  color: $color-text;
  border: 8px solid $color-background-black;
  outline: 1px solid $color-form-border;
  box-shadow: 0 1.5rem 4rem rgba($color-background-black, 0.6);
  box-sizing: border-box;
  position: fixed;
  inset: 0;
  margin: auto;
  width: calc(100% - var(--yaml-modal-gap) * 2);
  max-width: none;
  height: calc(100dvh - var(--yaml-modal-gap) * 2);
  max-height: none;
  padding: 0;
  overflow: hidden;
  &[open] { display: flex; flex-direction: column; }
  &::backdrop { background: rgba($color-background-black, 0.6); }
  button { flex-shrink: 0; }
}
.yaml-viewer-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  padding: 1.25rem 1.5rem;
  background: $color-background-header;
  h2 { margin: 0 0 0.35rem; }
  p { color: $color-text-hex-70; margin: 0; font-size: 0.85rem; }
  .icon-button { min-width: 44px; min-height: 44px; border: 1px solid $color-form-border; color: $color-text; }
}
.yaml-viewer-draft { display: inline-block; color: $color-secondary; font-size: 0.8rem; margin-top: 0.4rem; }
.yaml-viewer-toolbar, .yaml-viewer-modes, .yaml-viewer-tools { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
.yaml-viewer-toolbar { padding: 0.8rem 1.5rem; justify-content: space-between; border-bottom: 1px solid $color-form-border; }
.yaml-viewer-modes [aria-pressed='true'] { background: $color-secondary; border-color: $color-secondary; color: $color-text-dark; }
.yaml-viewer-copy-status { font-size: 0.8rem; color: $color-text-hex-80; }
.yaml-viewer-copy-status:empty { display: none; }
.yaml-viewer-search {
  display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem 1rem; padding: 0.8rem 1.5rem; margin: 0;
  label { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; padding: 0; }
  input { min-width: 0; width: min(100%, 26rem); box-sizing: border-box; }
}
.yaml-viewer-hint, .yaml-viewer-footer { color: $color-text-hex-70; font-size: 0.8rem; }
.yaml-viewer-content { flex: 1; min-height: 0; overflow: auto; padding: 0.5rem 1.5rem 1.5rem; }
.yaml-viewer-row {
  display: grid;
  grid-template-columns: minmax(12rem, 0.8fr) minmax(0, 1.8fr) 3rem;
  gap: 0.5rem 1.25rem;
  align-items: start;
  padding: 0.7rem 0.75rem 0.7rem calc(0.75rem + var(--yaml-depth) * 1rem);
  border-bottom: 1px solid $color-background-light-border;
  font-size: 0.9rem;
}
.yaml-viewer-section { background: $color-background-light; }
.yaml-viewer-document { margin-top: 0.75rem; border-left: 3px solid $color-secondary; }
.yaml-viewer-field, .yaml-viewer-value { min-width: 0; overflow-wrap: anywhere; }
.yaml-viewer-disclosure {
  display: flex; gap: 0.4rem; align-items: flex-start; text-align: left;
  color: $color-secondary; font: inherit; border: 0; padding: 0; background: none; cursor: pointer;
  svg { flex-shrink: 0; margin-top: 0.15rem; }
  &:disabled { cursor: default; }
}
.yaml-viewer-label { color: $color-secondary; }
.yaml-viewer-key { display: block; @include font-mono; font-size: 0.7rem; color: $color-text-hex-70; margin-top: 0.2rem; }
.yaml-viewer-value { white-space: pre-wrap; }
.yaml-viewer-note { display: block; color: $color-text-hex-70; font-size: 0.8rem; margin-top: 0.5rem; }
.yaml-viewer-line { text-align: right; color: $color-text-hex-70; @include font-mono; font-size: 0.65rem; padding-top: 0.2rem; }
.yaml-color-key { color: $color-secondary; }
.yaml-color-string { color: $color-text; }
.yaml-color-number { color: $color-green-chat; }
.yaml-color-boolean, .yaml-color-alias { color: $color-secondary; }
.yaml-color-null, .yaml-color-map, .yaml-color-list, .yaml-color-plain { color: $color-text-hex-70; }
.yaml-viewer-source {
  display: flex;
  pre, textarea { flex: 1; margin: 0; padding: 1rem; font-family: $font-mono, monospace; font-size: 0.85rem; line-height: 1.6; color: $color-text; background: $color-background; border: 0; min-width: 0; box-sizing: border-box; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; }
  code { background: none; padding: 0; border: 0; color: inherit; font: inherit; }
  textarea { width: 100%; resize: none; }
}
.yaml-viewer-error { margin: 0.5rem 1.5rem; padding: 0.75rem; border: 1px solid $color-secondary; white-space: pre-wrap; font-size: 0.85rem; max-height: 7rem; overflow: auto; }
.yaml-viewer-more { margin-top: 1rem; }
.yaml-viewer-footer { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.4rem; padding: 0.65rem 1.5rem; border-top: 1px solid $color-form-border; }
@media ($mobile-site) {
  .yaml-viewer { --yaml-modal-gap: 0.75rem; border-width: 4px; }
  .yaml-viewer-header { padding: 0.75rem; }
  .yaml-viewer-toolbar, .yaml-viewer-search { padding: 0.65rem 0.75rem; }
  .yaml-viewer-search input { width: 100%; }
  .yaml-viewer-search .yaml-viewer-hint { display: none; }
  .yaml-viewer-content { padding: 0.25rem 0.5rem 1rem; }
  .yaml-viewer-row { grid-template-columns: minmax(0, 1fr) 2.5rem; gap: 0.4rem; padding: 0.65rem 0.5rem 0.65rem calc(0.5rem + var(--yaml-depth) * 0.4rem); }
  .yaml-viewer-value { grid-column: 1; }
  .yaml-viewer-line { grid-column: 2; grid-row: 1; }
  .yaml-viewer-source pre, .yaml-viewer-source textarea { padding: 0.5rem; font-size: 0.75rem; }
  .yaml-viewer-footer { padding: 0.5rem 0.75rem; font-size: 0.7rem; }
}
</style>
