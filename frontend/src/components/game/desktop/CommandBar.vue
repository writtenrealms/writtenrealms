<template>
  <div class="desktop-command-bar">
    <div v-if="control?.enabled" class="combat-pause-control">
      <span v-if="error" class="command-error" role="alert">{{ error }}</span>
      <CombatPauseToggle compact dense />
    </div>
    <Input @input="emit('input', $event)">
      <template v-if="control?.paused" #action>
        <button
          type="button"
          class="instance-action"
          :disabled="!canAdvance"
          :title="actionTitle"
          :aria-label="actionText ? `Advance turn — Action: ${actionText}` : 'Advance turn without a queued action'"
          @click="advance"
        >
          <span v-if="actionText" class="action-prefix">Action:</span>
          <span class="action-text">{{ actionText || 'Advance Turn' }}</span>
          <span aria-hidden="true" class="action-arrow">→</span>
        </button>
        <button
          v-if="control.pending_command"
          type="button"
          class="clear-instance-action"
          :disabled="!canClear"
          title="Clear queued action"
          aria-label="Clear queued action"
          @click="clearAction"
        ><span aria-hidden="true">×</span></button>
      </template>
    </Input>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useStore } from 'vuex';
import Input from '@/components/game/Input.vue';
import CombatPauseToggle from '@/components/game/CombatPauseToggle.vue';
import {
  advanceTurnCommand, cancelTurnCommand, instanceActionText, pendingTurnCommandState, type InstanceTimeControl,
} from '@/core/instanceTimeControl';

const store = useStore();
const emit = defineEmits(['input']);
const control = computed<InstanceTimeControl | null>(() => store.state.game.instance_time_control);
const actionText = computed(() => instanceActionText(control.value));
const pending = computed(() => store.state.game.time_control_request);
const connected = computed(() => store.state.game.is_connected);
const error = computed(() => store.state.game.time_control_error);
const commandState = computed(() => pendingTurnCommandState(store.state.game.messages || []));
const canAdvance = computed(() => !!(control.value?.paused && control.value?.can_advance
  && connected.value && !pending.value && !commandState.value));
const canClear = computed(() => !!(control.value?.paused && control.value?.pending_command
  && connected.value && !pending.value && !commandState.value));
const actionTitle = computed(() => {
  if (!connected.value || commandState.value === 'uncertain') return 'Reconnect before advancing.';
  if (pending.value || commandState.value) return 'Waiting for your command to be confirmed.';
  return actionText.value ? `Advance turn with ${actionText.value}` : 'Advance turn without a queued action';
});
const advance = () => {
  if (!canAdvance.value || !control.value) return;
  store.dispatch('game/time_control_command', advanceTurnCommand(control.value));
  document.getElementById('console-input')?.focus();
};
const clearAction = () => {
  if (!canClear.value || !control.value) return;
  store.dispatch('game/time_control_command', cancelTurnCommand(control.value));
  document.getElementById('console-input')?.focus();
};
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';

.desktop-command-bar { position: relative; min-width: 0; }
:deep(#input) { padding: 0 20px; }
.combat-pause-control {
  position: absolute;
  right: 20px;
  bottom: calc(100% + 6px);
  z-index: 101;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: calc(100% - 40px);
  padding: 2px 8px;
  border: 1px solid $color-background-light-border;
  border-radius: 3px;
  background: $color-background;
  color: $color-text-hex-70;
}
.command-error { max-width: 320px; padding: 4px 0; color: $color-red; font-size: 12px; }
.instance-action {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 50%;
  margin: 3px;
  padding: 0 10px;
  border: 0;
  border-radius: 2px;
  background: $color-background-light;
  color: $color-secondary;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.action-prefix, .action-arrow { flex-shrink: 0; }
.action-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.clear-instance-action {
  flex: 0 0 28px;
  margin: 3px 3px 3px 0;
  padding: 0;
  border: 0;
  border-radius: 2px;
  background: $color-background-light;
  color: $color-secondary;
  font: inherit;
  font-size: 18px;
  cursor: pointer;
}
.instance-action, .clear-instance-action {
  &:hover:enabled { background: $color-background-very-light; }
  &:disabled { opacity: 0.45; cursor: default; }
  &:focus-visible { outline: 2px solid $color-secondary; outline-offset: 1px; }
}
</style>
