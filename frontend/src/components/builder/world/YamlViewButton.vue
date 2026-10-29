<template>
  <button v-bind="$attrs" type="button" class="btn-small" :disabled="disabled || !source.trim()" @click="open = true">
    VIEW YAML
  </button>
  <YamlViewer v-if="open" :source="source" :title="title" :has-changes="hasChanges" @close="open = false" />
</template>

<script setup lang="ts">
import { defineAsyncComponent, ref } from 'vue';
defineOptions({ inheritAttrs: false });

withDefaults(defineProps<{
  source: string;
  title?: string;
  disabled?: boolean;
  hasChanges?: boolean;
}>(), { title: 'YAML viewer', disabled: false, hasChanges: false });

const open = ref(false);
const YamlViewer = defineAsyncComponent(() => import('./YamlViewer.vue'));
</script>
