<template>
  <section v-if="control?.enabled" class="time-control" :class="{ compact }" aria-label="Instance time control" :aria-busy="!!pending">
    <template v-if="compact">
      <span v-if="compactError" class="compact-error" role="alert">
        <button type="button" class="icon-button" :aria-label="compactError" :title="compactError" @click="showError"><CircleAlert aria-hidden="true" /></button>
      </span>
      <button v-if="control.paused" type="button" class="compact-action" :disabled="!canAdvance" :title="actionTitle" :aria-label="actionTitle" @click="advance">
        <span class="compact-action-text">{{ pending ? 'Sending…' : commandState === 'awaiting' ? 'Waiting…' : actionText ? `Action: ${actionText}` : 'Advance Turn' }}</span>
        <ArrowRight aria-hidden="true" />
      </button>
      <button v-if="control.paused && control.pending_command" type="button" class="icon-button compact-clear" :disabled="!canClear" title="Clear queued action" aria-label="Clear queued action" @click="cancel"><X aria-hidden="true" /></button>
      <CombatPauseToggle compact dense />
    </template>
    <template v-else>
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
        <button v-if="control.pending_command" type="button" class="btn-thin" :disabled="!canClear" @click="cancel">Clear action</button>
        <button v-if="control.paused" type="button" class="btn-small advance-turn" :disabled="!canAdvance" @click="advance">Advance Turn</button>
        <CombatPauseToggle compact />
        <button type="button" class="pace-settings" @click="openSettings">Settings</button>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { ArrowRight, CircleAlert, X } from "@lucide/vue";
import { useStore } from "vuex";
import { advanceTurnCommand, cancelTurnCommand, instanceActionText, pendingTurnCommandState, type InstanceTimeControl } from "@/core/instanceTimeControl";
import CombatPauseToggle from "@/components/game/CombatPauseToggle.vue";
import Settings from "@/components/game/Settings.vue";

defineProps<{ compact?: boolean }>();
const store = useStore();
const control = computed<InstanceTimeControl | null>(() => store.state.game.instance_time_control);
const actionText = computed(() => instanceActionText(control.value));
const pending = computed(() => store.state.game.time_control_request);
const error = computed(() => store.state.game.time_control_error);
const connected = computed(() => store.state.game.is_connected);
const commandState = computed(() => pendingTurnCommandState(store.state.game.messages || []));
const canAdvance = computed(() => !!(control.value?.paused && control.value.can_advance
  && connected.value && !pending.value && !commandState.value));
const canClear = computed(() => !!(control.value?.pending_command
  && connected.value && !pending.value && !commandState.value));
const compactError = computed(() => error.value || (commandState.value === 'uncertain'
  ? 'A command is unconfirmed. Reconnect before advancing.' : ''));
const actionTitle = computed(() => {
  if (!connected.value || commandState.value === 'uncertain') return 'Reconnect before advancing.';
  if (pending.value || commandState.value) return 'Waiting for your command to be confirmed.';
  return actionText.value ? `Advance turn — Action: ${actionText.value}` : 'Advance turn without a queued action';
});
const showError = () => store.commit('ui/notification_set_error', compactError.value);
const advance = () => {
  if (!control.value || !canAdvance.value) return;
  store.dispatch("game/time_control_command", advanceTurnCommand(control.value));
};
const cancel = () => {
  if (!control.value || !canClear.value) return;
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
.time-control.compact {
  flex-wrap: nowrap;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
  padding: 2px 12px;
  border-color: $color-background-light-border;
  color: $color-text-hex-70;

  :deep(.combat-pause-toggle) { flex-shrink: 0; margin-left: auto; }
  :deep(.combat-pause-toggle label) { min-height: 32px; white-space: nowrap; }
  .icon-button { flex: 0 0 32px; width: 32px; height: 32px; padding: 0; }
}
.compact-action {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: 2px;
  background: $color-background-light;
  color: $color-secondary;
  font: inherit;
  font-size: 12px;
  cursor: pointer;

  svg { flex-shrink: 0; }
  &:hover:enabled { background: $color-background-very-light; }
}
.compact-action-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.compact-clear { color: $color-secondary; }
.compact-error {
  display: flex;
  flex-shrink: 0;
  .icon-button { color: $color-red; }
}
</style>
