<template>
  <div class="progress-bar">
    <div class="progress-fill" ref="progress" :style="{width: controlledWidth ?? initialWidth}"></div>
    <div class="progress-label">{{ label }}</div>
  </div>
</template>

<script lang='ts' setup>
import { computed, ref, onMounted, onUnmounted, watch } from "vue";
import { useStore } from "vuex";
import { gsap } from "gsap";
import { simulationTimeMs } from "@/core/instanceTimeControl";

const store = useStore();

const props = defineProps<{
  duration: number;
  label: string;
  expires?: number;
  start?: number;
  method: string;
}>();

const initialWidth = ref('0');
const progress = ref<HTMLElement | null>(null);
const controlledWidth = computed(() => {
  const now = simulationTimeMs(store.state.game.instance_time_control);
  if (now === null) return null;
  const durationMs = Math.max(1, props.duration * 1000);
  const start = props.start ?? ((props.expires ?? now + durationMs) - durationMs);
  const complete = Math.max(0, Math.min(100, (now - start) / durationMs * 100));
  return `${props.method === "channel" ? 100 - complete : complete}%`;
});
const mountedAt = Date.now();
const updateProgress = () => {
  if (!progress.value) return;
  gsap.killTweensOf(progress.value);
  const pausedNow = simulationTimeMs(store.state.game.instance_time_control);
  const now = pausedNow ?? Date.now();
  const durationMs = Math.max(1, props.duration * 1000);
  const start = props.start ?? (props.expires ? props.expires - durationMs : mountedAt);
  const complete = Math.max(0, Math.min(1, (now - start) / durationMs));
  const width = (props.method === "channel" ? 1 - complete : complete) * 100;
  initialWidth.value = `${width}%`;
  gsap.set(progress.value, { width: initialWidth.value });
  if (pausedNow !== null || complete >= 1) return;
  gsap.to(progress.value, {
    duration: durationMs * (1 - complete) / 1000,
    width: props.method === "channel" ? "0%" : "100%", ease: "none",
  });
};
watch([
  () => store.state.game.instance_time_control?.paused,
  () => store.state.game.instance_time_control?.simulation_time,
  () => props.start, () => props.duration, () => props.expires,
], updateProgress);
onMounted(updateProgress);
onUnmounted(() => { if (progress.value) gsap.killTweensOf(progress.value); });
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
