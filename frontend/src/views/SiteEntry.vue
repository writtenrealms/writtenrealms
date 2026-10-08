<template>
  <div v-if="failed" class="loading-container" role="alert">
    <p>The lobby could not be loaded.</p>
    <button class="btn-small" @click="load">RETRY</button>
  </div>
  <component v-else-if="ready" :is="page" />
  <div v-else class="loading-container" role="status" aria-label="Loading lobby">
    <div class="spinner"></div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useStore } from 'vuex';
import { loadPlatformConfig, siteEntryDestination } from '@/core/platform';

const Home = defineAsyncComponent(() => import('@/views/Home.vue'));
const Lobby = defineAsyncComponent(() => import('@/views/lobby/Lobby.vue'));
const route = useRoute();
const router = useRouter();
const store = useStore();
const ready = ref(false);
const failed = ref(false);
const page = computed(() => route.name === 'lobby' ? Lobby : Home);

async function load() {
  if (!['home', 'homedirect', 'lobby'].includes(String(route.name))) return;
  const entryPath = route.fullPath;
  ready.value = false;
  failed.value = false;
  try {
    const config = await loadPlatformConfig();
    if (route.fullPath !== entryPath) return;
    const destination = siteEntryDestination(config, route, store.getters.isAuthenticated);
    if (destination) await router.replace(destination);
    else ready.value = true;
  } catch {
    if (route.fullPath === entryPath) failed.value = true;
  }
}

watch(() => route.fullPath, load, { immediate: true });
</script>
