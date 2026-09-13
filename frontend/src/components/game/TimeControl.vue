<template>
  <section v-if="control?.enabled" class="time-control" aria-label="Instance time control">
    <div class="turn-summary" aria-live="polite">
      <strong>{{ control.paused ? 'Combat paused' : 'Normal world timing' }}</strong>
      <span v-if="pending">Sending {{ pending.label }}…</span>
      <span v-else-if="commandState === 'uncertain'" role="alert">A command is unconfirmed. Reconnect before advancing.</span>
      <span v-else-if="commandState === 'awaiting'">{{ control.paused ? 'Waiting for your command to be prepared…' : 'Waiting for your command…' }}</span>
      <span v-else-if="control.paused">Prepare an action, then advance. The whole instance waits.</span>
      <span v-else-if="control.pause_in_combat">Combat will pause before every round.</span>
      <span v-if="control.paused" class="prepared-action">{{ actionText ? `Action: ${actionText}` : 'No action set.' }}</span>
      <span v-if="error" class="turn-error" role="alert">{{ error }}</span>
    </div>
    <div class="turn-actions">
      <button v-if="control.pending_command" type="button" class="btn-thin" :disabled="!!pending || !connected" @click="cancel">Clear action</button>
      <button v-if="control.paused" type="button" class="btn-small advance-turn" :disabled="!control.can_advance || !!pending || !!commandState || !connected" @click="advance">Advance Turn</button>
      <CombatPauseToggle compact />
      <button type="button" class="pace-settings" @click="openSettings">Settings</button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useStore } from "vuex";
import { advanceTurnCommand, cancelTurnCommand, instanceActionText, pendingTurnCommandState, type InstanceTimeControl } from "@/core/instanceTimeControl";
import CombatPauseToggle from "@/components/game/CombatPauseToggle.vue";
import Settings from "@/components/game/Settings.vue";

const store = useStore();
const control = computed<InstanceTimeControl | null>(() => store.state.game.instance_time_control);
const actionText = computed(() => instanceActionText(control.value));
const pending = computed(() => store.state.game.time_control_request);
const error = computed(() => store.state.game.time_control_error);
const connected = computed(() => store.state.game.is_connected);
const commandState = computed(() => pendingTurnCommandState(store.state.game.messages || []));
const advance = () => {
  if (!control.value || pending.value || commandState.value || !control.value.can_advance) return;
  store.dispatch("game/time_control_command", advanceTurnCommand(control.value));
};
const cancel = () => {
  if (!control.value || pending.value) return;
  store.dispatch("game/time_control_command", cancelTurnCommand(control.value));
};
const openSettings = () => store.commit("ui/modal/open_view", { component: Settings });
</script>

<style scoped lang="scss">
@import "@/styles/colors.scss";
.time-control {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
  padding: 12px 20px;
  border-top: 1px solid $color-primary-70;
  background: $color-background-black;
  font-size: 14px;
}
.turn-summary { display: flex; flex: 1 1 210px; flex-direction: column; gap: 3px; min-width: 0; }
.turn-summary strong { color: $color-secondary; }
.turn-summary span { color: $color-text-hex-70; }
.prepared-action { overflow-wrap: anywhere; }
.turn-summary .turn-error { color: $color-red; }
.turn-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.advance-turn { min-height: 36px; }
.pace-settings { background: transparent; border: 0; padding: 0; color: $color-primary; font: inherit; text-decoration: underline; cursor: pointer; }
button:disabled { opacity: 0.45; cursor: default; }
button:focus-visible { outline: 2px solid $color-secondary; outline-offset: 3px; }
@media (max-width: 600px) {
  .time-control { padding: 10px 12px; gap: 8px; }
  .turn-actions { width: 100%; }
  .advance-turn { flex: 1; min-height: 44px; }
}
</style>
