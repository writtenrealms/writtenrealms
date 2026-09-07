<template>
  <div v-if="control?.enabled" class="combat-pause-toggle">
    <label>
      <input type="checkbox" :checked="control.pause_in_combat" :disabled="!connected || !!pending" @change="toggle" />
      <span>{{ compact ? 'Pause combat' : 'Pause before every combat round' }}</span>
    </label>
    <span v-if="error && !compact" class="error" role="alert">{{ error }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useStore } from "vuex";
import { combatPauseCommand, type InstanceTimeControl } from "@/core/instanceTimeControl";

defineProps<{ compact?: boolean }>();
const store = useStore();
const control = computed<InstanceTimeControl | null>(() => store.state.game.instance_time_control);
const connected = computed(() => store.state.game.is_connected);
const pending = computed(() => store.state.game.time_control_request);
const error = computed(() => store.state.game.time_control_error);
const toggle = (event: Event) => {
  if (!control.value || pending.value || !connected.value) return;
  store.dispatch("game/time_control_command", combatPauseCommand(control.value, (event.target as HTMLInputElement).checked));
};
</script>

<style scoped lang="scss">
.combat-pause-toggle label { display: flex; align-items: center; gap: 8px; min-height: 44px; cursor: pointer; }
input { width: 20px; height: 20px; margin: 0; accent-color: #bb9865; }
input:focus-visible { outline: 2px solid currentColor; outline-offset: 3px; }
input:disabled { cursor: default; }
.error { display: block; color: #eb5757; }
</style>
