<template>
  <Teleport to="body">
    <div
      class="intro-overlay"
      ref="introRef"
      role="dialog"
      aria-modal="true"
      aria-label="World introduction"
      aria-describedby="intro-dismiss"
      tabindex="-1"
      @click="emit('close')"
    >
      <div class="intro-prologue">
        <div class="prologue-text" aria-live="polite" aria-atomic="true">
          <p v-for="(paragraph, index) in visibleParagraphs" :key="index">{{ paragraph }}</p>
        </div>
        <div id="intro-dismiss" class="click-anywhere">CLICK OR TAP ANYWHERE TO BEGIN</div>
      </div>
    </div>
  </Teleport>
</template>

<script lang="ts" setup>
import { nextTick, onMounted, onBeforeUnmount, ref } from "vue";
import { startEntryMessage, type WorldEntryMessage } from "@/core/worldEntryMessage";

const props = defineProps<{ message: WorldEntryMessage }>();
const emit = defineEmits<{ close: [] }>();
const introRef = ref<HTMLElement | null>(null);
const visibleParagraphs = ref<string[]>([]);
let stopPlayback = () => {};
let previousFocus: HTMLElement | null = null;

const onKeyDown = (event: KeyboardEvent) => {
  // Capture before game shortcuts so reading an introduction cannot move a player.
  event.stopImmediatePropagation();
  if (["Escape", "Enter", " "].includes(event.key)) {
    event.preventDefault();
    emit("close");
  } else if (event.key === "Tab") {
    event.preventDefault();
    introRef.value?.focus();
  }
};

onMounted(() => {
  previousFocus = document.activeElement as HTMLElement | null;
  introRef.value?.focus();
  window.addEventListener("keydown", onKeyDown, true);
  stopPlayback = startEntryMessage(props.message, paragraphs => { visibleParagraphs.value = paragraphs; });
});

onBeforeUnmount(() => {
  stopPlayback();
  window.removeEventListener("keydown", onKeyDown, true);
  nextTick(() => {
    if (previousFocus?.isConnected) previousFocus.focus();
  });
});
</script>

<style lang="scss" scoped>
@import "@/styles/fonts.scss";

.intro-overlay {
  position: fixed;
  inset: 0;
  z-index: 40000;
  background: #000;
  display: flex;
  overflow-y: auto;
  overscroll-behavior: contain;
  outline: none;
  cursor: pointer;
}

.intro-prologue {
  box-sizing: border-box;
  width: 100%;
  max-width: 760px;
  border: 10px solid #020202;
  padding: 15px;
  margin: auto;

  .prologue-text {
    @include font-text-regular;
    max-width: 400px;
    margin: 0 auto;
    white-space: pre-line;
    overflow-wrap: anywhere;

    p { margin: 0 0 1em; }
    p:last-child { margin-bottom: 0; }
  }

  .click-anywhere {
    @include font-title-light;
    margin-top: 50px;
    text-align: center;
  }
}
</style>
