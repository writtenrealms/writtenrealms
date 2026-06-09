<template>
  <div class="manifest-yaml-editor">
    <div class="manifest-yaml-editor-header mb-4">
      <div class="manifest-yaml-editor-heading">
        <slot name="header" />
      </div>

      <div class="manifest-yaml-editor-actions">
        <button class="btn-small" :disabled="!canCopy" @click="copyYaml">
          {{ copyLabel }}
        </button>
        <button class="btn-small primary-action" :disabled="!canSave" @click="emit('save')">
          {{ isSubmitting ? savingLabel : saveLabel }}
        </button>
      </div>
    </div>

    <textarea
      ref="textarea"
      :value="currentValue"
      class="manifest-yaml-editor-input"
      :style="textareaStyle"
      spellcheck="false"
      @input="onInput"
    />
  </div>
</template>

<script lang="ts" setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useStore } from "vuex";

const props = withDefaults(defineProps<{
  modelValue: string;
  loadedValue?: string;
  isSubmitting?: boolean;
  disabled?: boolean;
  copyLabel?: string;
  saveLabel?: string;
  savingLabel?: string;
  copySuccessMessage?: string;
  copyErrorMessage?: string;
  minHeight?: number;
  bottomGap?: number;
}>(), {
  loadedValue: "",
  isSubmitting: false,
  disabled: false,
  copyLabel: "COPY YAML",
  saveLabel: "SAVE YAML",
  savingLabel: "SAVING...",
  copySuccessMessage: "YAML copied.",
  copyErrorMessage: "Unable to copy YAML to clipboard.",
  minHeight: 260,
  bottomGap: 24,
});

const emit = defineEmits<{
  (event: "update:modelValue", value: string): void;
  (event: "save"): void;
}>();

const store = useStore();
const textarea = ref<HTMLTextAreaElement | null>(null);
const textareaHeight = ref("480px");

const currentValue = computed(() => props.modelValue || "");
const savedValue = computed(() => props.loadedValue || "");
const canCopy = computed(() => !props.disabled && Boolean(currentValue.value.trim()));
const canSave = computed(() => (
  !props.disabled
  && !props.isSubmitting
  && Boolean(currentValue.value.trim())
  && currentValue.value !== savedValue.value
));
const textareaStyle = computed(() => ({
  height: textareaHeight.value,
  maxHeight: textareaHeight.value,
  minHeight: `${props.minHeight}px`,
}));

const measureTextarea = async () => {
  await nextTick();
  const el = textarea.value;
  if (!el) return;
  const availableHeight = window.innerHeight - el.getBoundingClientRect().top - props.bottomGap;
  textareaHeight.value = `${Math.max(props.minHeight, Math.floor(availableHeight))}px`;
};

const onInput = (event: Event) => {
  emit("update:modelValue", (event.target as HTMLTextAreaElement).value);
};

const copyYaml = async () => {
  try {
    await navigator.clipboard.writeText(currentValue.value);
    store.commit("ui/notification_set", props.copySuccessMessage);
  } catch {
    store.commit("ui/notification_set_error", props.copyErrorMessage);
  }
};

onMounted(() => {
  window.addEventListener("resize", measureTextarea);
  measureTextarea();
});

onUnmounted(() => {
  window.removeEventListener("resize", measureTextarea);
});

watch(
  () => props.loadedValue,
  () => {
    measureTextarea();
  },
  { flush: "post" },
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.manifest-yaml-editor {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}

.manifest-yaml-editor-header {
  align-items: flex-start;
  display: flex;
  gap: 1rem;
  justify-content: space-between;
}

.manifest-yaml-editor-heading {
  min-width: 0;
}

.manifest-yaml-editor-actions {
  display: flex;
  flex-shrink: 0;
  gap: 0.5rem;

  button:disabled {
    border-color: $color-text-hex-50;
    color: $color-text-hex-50;
    cursor: default;
  }
}

.manifest-yaml-editor-input {
  box-sizing: border-box;
  width: 100%;
  padding: 0.75rem;
  border: 1px solid $color-form-border;
  background: $color-background;
  color: $color-text;
  font-family: monospace;
  line-height: 1.35;
  overflow: auto;
  resize: none;
}

.primary-action {
  background-color: $color-primary;
  border-color: $color-primary;
  color: white;

  &:hover {
    background-color: $color-primary-70;
    border-color: $color-primary-70;
    color: white;
  }

  &:disabled {
    background: transparent;
    border-color: $color-text-hex-50;
    color: $color-text-hex-50;
    cursor: default;
  }
}
</style>
