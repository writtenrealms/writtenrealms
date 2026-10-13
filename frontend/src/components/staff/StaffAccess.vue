<template>
  <div class="control-stack">
    <p v-if="error" class="inline-notice inline-notice-error" role="alert">{{ error }}</p>
    <p v-if="success" class="inline-notice" role="status">{{ success }}</p>
    <p v-if="loading && !settings" class="control-empty" role="status">Loading access settings…</p>
    <button v-if="!settings && !loading" class="btn-small" @click="loadSettings">RETRY SETTINGS</button>
    <template v-if="settings">
      <section class="panel control-section">
        <div class="control-section-heading"><div><h2>Platform access</h2><p>{{ accessSummary }}</p></div><span class="tag" :class="effectiveMode === 'open' ? 'tag-green' : 'tag-secondary'">{{ accessLabels[effectiveMode] }}</span></div>
        <form @submit.prevent="saveSettings">
          <fieldset class="access-options" :disabled="saving">
            <legend class="sr-only">Access policy</legend>
            <label v-for="option in accessOptions" :key="option.value" :class="{selected: draft.access_mode === option.value}">
              <input v-model="draft.access_mode" type="radio" name="access-mode" :value="option.value" />
              <span><strong>{{ option.label }}</strong><small>{{ option.description }}</small></span>
            </label>
          </fieldset>
          <p class="control-note">Access changes apply to sign-in, API requests, and game commands within {{ settings.access_propagation_seconds }} seconds. Existing connections may still receive updates until they disconnect.</p>
          <div class="control-form-grid">
            <div class="form-group checkbox"><label><input v-model="draft.maintenance_mode" type="checkbox" :disabled="saving" /> Maintenance mode</label><p class="control-note">Temporarily block game entry and world startup for non-staff. Existing players can continue.</p></div>
            <div class="form-group checkbox"><label><input v-model="draft.building_enabled" type="checkbox" :disabled="saving" /> Allow everyone to create worlds</label><p class="control-note">When disabled, existing builders keep access to their worlds.</p></div>
            <div class="form-group"><label for="main-world-id">Promoted world ID</label><input id="main-world-id" v-model="mainWorldDraft" type="number" min="1" step="1" :disabled="saving" placeholder="World directory" /><p class="control-note">Leave blank to show the world directory as the landing page.</p></div>
          </div>
          <div class="control-actions"><button class="btn-small" type="submit" :disabled="saving || !dirty">{{ saving ? 'SAVING…' : 'SAVE SETTINGS' }}</button><button class="btn-small button-gray" type="button" :disabled="saving || !dirty" @click="resetDraft">DISCARD CHANGES</button><span v-if="dirty" class="control-note">Unsaved changes</span></div>
        </form>
      </section>
      <section class="panel control-section">
        <div class="control-section-heading"><div><h2>Email whitelist</h2><p>{{ whitelistSummary }} Adding an address does not send email.</p></div><span class="tag">{{ allowlist.count }} addresses</span></div>
        <form class="allowlist-add control-form-grid" @submit.prevent="addEmail">
          <div class="form-group"><label for="invite-email">Email address</label><input id="invite-email" v-model="email" type="email" required maxlength="254" :disabled="adding" placeholder="name@example.com" /></div>
          <div class="form-group"><label for="invite-note">Note (optional)</label><input id="invite-note" v-model="note" maxlength="500" :disabled="adding" placeholder="Reason or reference" /></div>
          <button class="btn-small" type="submit" :disabled="adding || !email.trim()">{{ adding ? 'ADDING…' : 'ADD ADDRESS' }}</button>
        </form>
        <form class="control-search" @submit.prevent="loadAllowlist(1)"><div class="form-group"><label for="allowlist-search" class="sr-only">Find an allowed email</label><input id="allowlist-search" v-model="query" type="search" placeholder="Find an email" /></div><button class="btn-small button-gray" :disabled="listLoading">SEARCH</button></form>
        <p v-if="listError" class="inline-notice inline-notice-error" role="alert">{{ listError }} <button class="btn-small" @click="loadAllowlist(allowlist.page)">RETRY</button></p>
        <p v-if="listLoading" class="control-note" role="status">Loading addresses…</p>
        <div v-if="allowlist.results.length" class="data-table-scroll"><table class="data-table control-table"><thead><tr><th>Email</th><th>Note</th><th>Added</th><th></th></tr></thead><tbody><tr v-for="entry in allowlist.results" :key="entry.id"><th>{{ entry.email }}</th><td>{{ entry.note || '—' }}</td><td>{{ staffTime(entry.created_ts) }}<small>{{ entry.created_by_email }}</small></td><td><button class="icon-button" :aria-label="`Remove ${entry.email} from whitelist`" :disabled="removing !== null" @click="removeEmail(entry)"><Trash aria-hidden="true" /></button></td></tr></tbody></table></div>
        <p v-else-if="!listLoading && !listError" class="control-empty">{{ query ? 'No matching addresses.' : 'The email whitelist is empty.' }}</p>
        <div v-if="allowlist.count > allowlist.page_size" class="control-pagination"><button class="btn-small button-gray" :disabled="listLoading || allowlist.page <= 1" @click="loadAllowlist(allowlist.page - 1)">PREVIOUS</button><span>Page {{ allowlist.page }} of {{ Math.ceil(allowlist.count / allowlist.page_size) }}</span><button class="btn-small button-gray" :disabled="listLoading || allowlist.page * allowlist.page_size >= allowlist.count" @click="loadAllowlist(allowlist.page + 1)">NEXT</button></div>
      </section>
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { Trash } from '@lucide/vue';
import axios from 'axios';
import { platformConfig } from '@/core/platform';
import { accessLabels, changedStaffSettings, staffAccessOptions, staffError, staffTime, type AccessMode, type StaffAccessPolicy } from '@/core/staff';
interface Settings extends StaffAccessPolicy {
  maintenance_mode: boolean;
  building_enabled: boolean;
  main_world_id: number | null;
  access_propagation_seconds: number;
}
interface AllowedEmail {
  id: number;
  email: string;
  note: string;
  created_ts: string;
  created_by_email: string;
}
const emit = defineEmits<{ changed: [settings: Settings] }>();
const settings = ref<Settings | null>(null);
const draft = reactive({ access_mode: 'open' as AccessMode, maintenance_mode: false, building_enabled: true });
const mainWorldDraft = ref<string | number>('');
const error = ref('');
const success = ref('');
const loading = ref(false);
const saving = ref(false);
const email = ref('');
const note = ref('');
const query = ref('');
const adding = ref(false);
const removing = ref<number | null>(null);
const listLoading = ref(false);
const listError = ref('');
const allowlist = ref({ count: 0, page: 1, page_size: 25, results: [] as AllowedEmail[] });
let listRequest = 0;
const accessOptions = staffAccessOptions;
// Status describes the saved policy; changing a radio only changes the draft.
const effectiveMode = computed(() => settings.value?.access_mode ?? 'closed');
const accessSummary = computed(() => {
  if (effectiveMode.value === 'closed') {
    return 'Only staff can sign in and use the platform.';
  }
  return effectiveMode.value === 'open'
    ? 'Anyone can sign in and use the platform.'
    : 'Only staff and invited email addresses can sign in and use the platform.';
});
const whitelistSummary = computed(() => {
  return settings.value?.access_mode === 'invite_only'
    ? 'Only staff and emails on this list are admitted.'
    : 'This list only applies when Invite only is saved.';
});
const payload = computed(() => ({
  ...draft,
  main_world_id: mainWorldDraft.value === '' ? null : Number(mainWorldDraft.value),
}));
const dirty = computed(() => settings.value !== null &&
  Object.entries(payload.value).some(([key, value]) => value !== settings.value?.[key]));

function resetDraft() {
  if (!settings.value) return;
  Object.assign(draft, {
    access_mode: settings.value.access_mode,
    maintenance_mode: settings.value.maintenance_mode,
    building_enabled: settings.value.building_enabled,
  });
  mainWorldDraft.value = settings.value.main_world_id ?? '';
}

async function loadSettings() {
  loading.value = true;
  error.value = '';
  try {
    settings.value = (await axios.get('/staff/site/')).data;
    resetDraft();
    emit('changed', settings.value!);
  } catch (requestError) {
    error.value = staffError(requestError, 'Could not load access settings.');
  } finally {
    loading.value = false;
  }
}

async function saveSettings() {
  if (!dirty.value || saving.value) return;
  const accessChanged = payload.value.access_mode !== settings.value?.access_mode ||
    payload.value.maintenance_mode !== settings.value?.maintenance_mode;
  if (accessChanged && !window.confirm(
    `Apply this access policy${payload.value.maintenance_mode ? ' with maintenance mode' : ''}? ${accessOptions.find(option => option.value === payload.value.access_mode)!.description} Staff retain access.`
  )) return;
  saving.value = true;
  error.value = '';
  success.value = '';
  try {
    // Do not overwrite unrelated settings another administrator may have changed.
    const changes = changedStaffSettings(payload.value, { ...settings.value! });
    settings.value = (await axios.patch('/staff/site/', changes)).data;
    resetDraft();
    if (platformConfig.value) {
      platformConfig.value = {
        ...platformConfig.value,
        main_world_id: settings.value!.main_world_id,
        building_enabled: settings.value!.building_enabled,
      };
    }
    emit('changed', settings.value!);
    success.value = `Settings saved. Access changes take effect within ${settings.value!.access_propagation_seconds} seconds.`;
  } catch (requestError) {
    error.value = staffError(requestError, 'Could not save settings. Your changes are still here.');
  } finally {
    saving.value = false;
  }
}

async function loadAllowlist(page = 1) {
  const request = ++listRequest;
  listLoading.value = true;
  listError.value = '';
  try {
    const response = await axios.get('/staff/allowlist/', { params: { q: query.value.trim(), page } });
    if (request === listRequest) allowlist.value = response.data;
  } catch (requestError) {
    if (request === listRequest) listError.value = staffError(requestError, 'Could not load the whitelist.');
  } finally {
    if (request === listRequest) listLoading.value = false;
  }
}

async function addEmail() {
  if (adding.value) return;
  adding.value = true;
  error.value = '';
  success.value = '';
  try {
    await axios.post('/staff/allowlist/', { email: email.value.trim(), note: note.value.trim() });
    email.value = '';
    note.value = '';
    query.value = '';
    await loadAllowlist(1);
    success.value = 'Address added to the whitelist.';
  } catch (requestError) {
    error.value = staffError(requestError, 'Could not add this address.');
  } finally {
    adding.value = false;
  }
}

async function removeEmail(entry: AllowedEmail) {
  if (!window.confirm(`Remove ${entry.email} from the whitelist? They may lose platform access in invite-only mode.`)) return;
  removing.value = entry.id;
  error.value = '';
  success.value = '';
  try {
    await axios.delete(`/staff/allowlist/${entry.id}/`);
    const page = allowlist.value.results.length === 1 ? Math.max(1, allowlist.value.page - 1) : allowlist.value.page;
    await loadAllowlist(page);
    success.value = 'Address removed from the whitelist.';
  } catch (requestError) {
    error.value = staffError(requestError, 'Could not remove this address.');
  } finally {
    removing.value = null;
  }
}

onBeforeRouteLeave(() => !dirty.value || window.confirm('Discard your unsaved access settings?'));
onMounted(() => {
  void loadSettings();
  void loadAllowlist();
});
</script>
