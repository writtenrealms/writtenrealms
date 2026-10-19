<template>
  <main class="world-information">
    <router-link class="information-back" :to="{ name: 'lobby_world_details', params: { world_id: route.params.world_id } }">
      <ArrowLeft :size="16" aria-hidden="true" /> Back to {{ world?.name || 'world lobby' }}
    </router-link>

    <div v-if="loadError" role="alert">
      <p>{{ loadError }}</p>
      <router-link v-if="!store.getters.isAuthenticated" :to="{ name: 'login', query: { redirect: route.fullPath } }">Log in</router-link>
      <button class="btn-small button-gray" @click="loadWorld">RETRY</button>
    </div>
    <div v-else-if="!world" class="loading-container"><div class="spinner"></div></div>
    <template v-else>
      <header class="information-heading">
        <div class="information-title">
          <h1>About {{ world.name }}</h1>
          <p v-if="world.built_by?.trim()" class="information-credit color-text-60">Built by {{ world.built_by }}</p>
        </div>
        <button v-if="world.can_edit && !editing" class="btn-small button-gray" @click="startEditing">EDIT</button>
      </header>

      <p v-if="saveMessage" role="status" class="inline-notice">{{ saveMessage }}</p>
      <form v-if="editing" @submit.prevent="save">
        <div class="form-group">
          <label for="world-information-text">World information</label>
          <p id="information-help" class="color-text-70">Tell players about the setting, rules, and how to get started. Use Markdown for headings, lists, links, and images.</p>
          <div class="information-editor-tools">
            <button type="button" class="btn-thin" :aria-pressed="preview" @click="preview = !preview">{{ preview ? 'WRITE' : 'PREVIEW' }}</button>
            <span class="color-text-60">{{ draft.length.toLocaleString() }} / 50,000</span>
          </div>
          <textarea v-show="!preview" id="world-information-text" ref="editor" v-model="draft" rows="18" maxlength="50000"
            aria-describedby="information-help" :disabled="saving" placeholder="## Welcome&#10;&#10;Introduce your world to new players."></textarea>
          <div v-if="preview" class="information-preview" aria-label="Markdown preview">
            <MarkdownContent v-if="draft.trim()" :source="draft" />
            <p v-else class="color-text-60">Nothing to preview yet.</p>
          </div>
        </div>
        <p v-if="saveError" role="alert">{{ saveError }}</p>
        <div class="information-actions">
          <button class="btn-small" :disabled="saving">{{ saving ? 'SAVING…' : 'SAVE' }}</button>
          <button type="button" class="btn-small button-gray" :disabled="saving" @click="cancel">CANCEL</button>
        </div>
      </form>
      <MarkdownContent v-else-if="world.information.trim()" :source="world.information" />
      <p v-else class="color-text-70">The builders haven't added more information about this world yet.</p>
    </template>
  </main>
</template>

<script lang="ts" setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router';
import { useStore } from 'vuex';
import axios from 'axios';
import { ArrowLeft } from '@lucide/vue';
import MarkdownContent from '@/components/MarkdownContent.vue';

interface Information { id: number; name: string; built_by: string; information: string; can_edit: boolean }
const route = useRoute();
const store = useStore();
const world = ref<Information | null>(null);
const draft = ref('');
const editing = ref(false);
const preview = ref(false);
const saving = ref(false);
const loadError = ref('');
const saveError = ref('');
const saveMessage = ref('');
const editor = ref<HTMLTextAreaElement | null>(null);
const dirty = computed(() => editing.value && draft.value !== world.value?.information);
let requestVersion = 0;

async function loadWorld() {
  const version = ++requestVersion;
  world.value = null;
  editing.value = false;
  saving.value = false;
  loadError.value = saveError.value = saveMessage.value = '';
  try {
    const response = await axios.get(`/lobby/worlds/${route.params.world_id}/information/`);
    if (version !== requestVersion) return;
    world.value = response.data;
    if (world.value?.can_edit && !world.value.information.trim()) startEditing();
  } catch (error: any) {
    if (version !== requestVersion) return;
    const status = error.response?.status;
    loadError.value = status === 401 || status === 403
      ? 'You need an account with access to this world to read its information.'
      : status === 404 ? 'This world is unavailable.' : 'World information could not be loaded. Please try again.';
  }
}

async function startEditing() {
  draft.value = world.value?.information || '';
  editing.value = true;
  preview.value = false;
  saveError.value = saveMessage.value = '';
  await nextTick();
  editor.value?.focus({ preventScroll: true });
}

function cancel() {
  editing.value = false;
  saveError.value = '';
  draft.value = world.value?.information || '';
}

async function save() {
  if (!world.value?.can_edit || saving.value) return;
  const version = requestVersion;
  saving.value = true;
  saveError.value = '';
  try {
    const response = await axios.patch(`/lobby/worlds/${route.params.world_id}/information/`, { information: draft.value });
    if (version !== requestVersion) return;
    world.value.information = response.data.information;
    editing.value = false;
    saveMessage.value = 'World information saved.';
  } catch (error: any) {
    if (version !== requestVersion) return;
    saveError.value = error.response?.status === 401 || error.response?.status === 403
      ? 'You no longer have permission to edit this world. Your draft is still here.'
      : 'Your changes could not be saved. Your draft is still here; please try again.';
  } finally {
    if (version === requestVersion) saving.value = false;
  }
}

const confirmLeave = () => !dirty.value || window.confirm('Leave without saving your world information?');
onBeforeRouteLeave(confirmLeave);
onBeforeRouteUpdate((to, from) => to.params.world_id === from.params.world_id || confirmLeave());
const beforeUnload = (event: BeforeUnloadEvent) => {
  if (!dirty.value) return;
  event.preventDefault();
  event.returnValue = '';
};
onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload);
  window.scrollTo(0, 0);
});
onBeforeUnmount(() => {
  requestVersion++;
  window.removeEventListener('beforeunload', beforeUnload);
});
watch(() => route.params.world_id, loadWorld, { immediate: true });
</script>

<style lang="scss" scoped>
@import '@/styles/colors.scss';
@import '@/styles/fonts.scss';
@import '@/styles/layout.scss';

.world-information {
  box-sizing: border-box;
  width: 100%;
  max-width: 860px;
  margin: 0 auto;
  padding: 36px 30px 80px;
  @media ($mobile-site) { padding: 24px 15px 50px; }
}
.information-back { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; }
.information-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 26px 0 32px;
  padding-bottom: 24px;
  border-bottom: 1px solid $color-background-light-border;
  .information-title { min-width: 0; }
  h1 { @include font-title-regular; min-width: 0; font-size: 40px; line-height: 1.2; margin: 0; overflow-wrap: anywhere; }
  button { flex-shrink: 0; }
  @media ($mobile-site) { h1 { font-size: 32px; } }
}
.information-credit { margin: 8px 0 0; font-size: 16px; line-height: 1.5; overflow-wrap: anywhere; }
#information-help { font-size: 14px; line-height: 1.7; margin: 8px 0 16px; }
.information-editor-tools { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-size: 13px; }
#world-information-text { @include font-mono; line-height: 1.6; min-height: 360px; resize: vertical; }
.information-preview { min-height: 360px; padding: 20px; border: 1px solid $color-form-border; border-radius: 4px; }
.information-actions { display: flex; gap: 12px; margin-top: 20px; }
.inline-notice { margin-bottom: 24px; }
</style>
