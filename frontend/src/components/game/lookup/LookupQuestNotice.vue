<template>
  <div class="lookup-desktop lookup-quest-notice">
    <div class="name">Quest notice</div>
    <div class="description">{{ entity.text }}</div>
    <div class="actions">
      <button type="button" class="action primary" @click="inspect">INSPECT</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useStore } from 'vuex';

const props = defineProps<{ entity: { text: string; command?: string } }>();
const store = useStore();

const inspect = () => {
  store.dispatch('game/cmd', props.entity.command || 'inspect');
  store.commit('game/lookup_clear');
  store.commit('ui/modal/close');
};
</script>
