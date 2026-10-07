<template>
    <div id="app">
      <Notification v-if="notification" />

      <Header v-if="showHeader" />

      <div id="main">
        <router-view />
      </div>

      <Modal v-if="isModalOpen"/>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useStore } from 'vuex';
import Notification from '@/components/ui/Notification.vue';
import Modal from '@/components/ui/Modal.vue';
import Header from '@/components/Header.vue'
import { setLucideProps } from '@lucide/vue';

const route = useRoute();

// Defaults for every @lucide/vue icon in the app; pass :size to override.
setLucideProps({ size: 18, strokeWidth: 1.75 });
const store = useStore();

const notification = computed(() => store.state.ui.notification);
const isModalOpen = computed(() => store.state.ui.modal.isOpen);

const showHeader = computed(() => {
  return !["home", "game", "homedirect"].includes(route.name as string);
})
</script>

<style lang="scss" scoped>
// @import "styles/app.scss";
</style>