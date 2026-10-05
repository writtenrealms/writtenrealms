<template>
  <ModalView id="coms_log">
    <h1 class="mb-6">Communication Log</h1>
    <template v-if="log_entries.length">
      <Chat v-for="(entry, index) in log_entries" :key="index" :message="entry" class="mb-3" />
    </template>
    <div v-else>No communication entries yet. Use 'ask' to ask a question, or 'listen' to see available channels.</div>
  </ModalView>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import ModalView from "@/components/ui/ModalView.vue";
import Chat from "@/components/game/console/Chat.vue";

const store = useStore();

const log_entries = computed(() => store.state.game.com_list.slice().reverse());

</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
