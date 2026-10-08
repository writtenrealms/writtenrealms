<template>
  <Teleport to="body">
    <component :is="preview.centered ? 'dialog' : 'div'" id="item-preview" ref="panel"
      class="item-preview lookup-item" :class="preview.centered ? 'lookup-mobile centered' : 'lookup-desktop'"
      :style="position" role="dialog" :aria-label="`${preview.item.name} details`" tabindex="-1"
      @pointerenter="$emit('keep-open')" @pointerleave="$emit('leave')"
      @focusin="$emit('keep-open')" @focusout="onFocusOut" @cancel.prevent="dismiss">
      <button class="icon-button preview-close" aria-label="Close item details" @click="dismiss">
        <X aria-hidden="true" />
      </button>
      <ItemInfo :item="preview.item" :player="player" :world="world" :contentsInteractive="false" />
    </component>
  </Teleport>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { X } from "@lucide/vue";
import ItemInfo from "@/components/game/ItemInfo.vue";
import { itemPreviewPosition } from "@/composables/useItemPreview";
import type { ItemPreview } from "@/composables/useItemPreview";

const props = defineProps<{ preview: ItemPreview; player: any; world: any }>();
const emit = defineEmits(["close", "keep-open", "leave"]);
const panel = ref<HTMLElement | null>(null);
const position = ref({});
let previousOverflow: string | undefined;

const dismiss = () => {
  // Restore keyboard/tap focus without reopening the preview via its focus handler.
  const restoreFocus = props.preview.centered || panel.value?.contains(document.activeElement);
  if (props.preview.centered) (panel.value as HTMLDialogElement)?.close();
  if (restoreFocus) {
    props.preview.anchor.focus({ preventScroll: true });
  }
  emit("close");
};
const onKeyDown = (event: KeyboardEvent) => {
  if (event.key === "Escape") dismiss();
};
const onOutsidePointer = (event: PointerEvent) => {
  const target = event.target as Node;
  if (props.preview.anchor.contains(target)) return;
  if (panel.value?.contains(target)) {
    // Native dialog backdrops target the dialog itself.
    if (!props.preview.centered || target !== panel.value) return;
    const rect = panel.value.getBoundingClientRect();
    if (event.clientX >= rect.left && event.clientX <= rect.right
      && event.clientY >= rect.top && event.clientY <= rect.bottom) return;
  }
  dismiss();
};
const onScroll = (event: Event) => {
  if (!props.preview.centered && !panel.value?.contains(event.target as Node)) emit("close");
};
const onResize = () => emit("close");
const onFocusOut = (event: FocusEvent) => {
  if (!panel.value?.contains(event.relatedTarget as Node)) emit("leave");
};
const display = async () => {
  await nextTick();
  if (!panel.value) return;
  if (props.preview.centered) {
    const dialog = panel.value as HTMLDialogElement;
    if (!dialog.open) dialog.showModal();
    if (previousOverflow === undefined) previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    position.value = {};
  } else {
    position.value = itemPreviewPosition(
      props.preview.anchor.getBoundingClientRect(), panel.value.getBoundingClientRect(),
      { width: window.innerWidth, height: window.innerHeight },
    );
    if (props.preview.focus) panel.value.focus({ preventScroll: true });
  }
};

watch(() => props.preview, display);
onMounted(() => {
  display();
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("pointerdown", onOutsidePointer);
  document.addEventListener("scroll", onScroll, true);
  window.addEventListener("resize", onResize);
});
onBeforeUnmount(() => {
  if (previousOverflow !== undefined) document.body.style.overflow = previousOverflow;
  document.removeEventListener("keydown", onKeyDown);
  document.removeEventListener("pointerdown", onOutsidePointer);
  document.removeEventListener("scroll", onScroll, true);
  window.removeEventListener("resize", onResize);
});
</script>

<style scoped lang="scss">
@import "@/styles/colors.scss";

.item-preview {
  position: fixed;
  z-index: 20000;
  box-sizing: border-box;
  width: 324px;
  max-width: calc(100vw - 24px);
  max-height: calc(100dvh - 24px);
  overflow: auto;
  overscroll-behavior: contain;
  overflow-wrap: anywhere;
  margin: 0;
  padding: 14px;
  border: 3px solid $color-background-very-light;
  background: $color-background-light;
  color: $color-text;

  &.centered {
    margin: auto;
    padding: 20px;
    background: $color-background-black;
  }

  &::backdrop {
    background: rgba(0, 0, 0, 0.6);
  }

  .preview-close {
    float: right;
    margin: -6px -6px 4px 8px;
  }
}
</style>
