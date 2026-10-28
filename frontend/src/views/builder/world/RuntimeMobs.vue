<template>
  <div class="dashboard-view runtime-mobs">
    <header class="dashboard-heading">
      <div>
        <router-link :to="adminPath" class="dashboard-note">← World #{{ route.params.instance_id }}</router-link>
        <div class="dashboard-eyebrow">WORLD ADMINISTRATION</div><h1>Loaded mobs</h1>
        <p>Inspect the mobs currently in this world.</p>
      </div>
      <button class="btn-small button-gray" :disabled="list.loading.value || detail.loading.value" @click="refresh"><RefreshCw :size="13" aria-hidden="true" /> REFRESH</button>
    </header>
    <div class="mob-browser" :class="{ 'has-selection': selectedId }">
      <section class="mob-browser-list panel dashboard-section" aria-label="Loaded mobs">
        <form class="mob-search" @submit.prevent="search">
          <div class="form-group">
            <label for="mob-search">Find a mob</label>
            <input id="mob-search" v-model="query" type="search" maxlength="120" placeholder="Name, ID, room, or definition" />
          </div>
          <button class="btn-small button-gray" :disabled="list.loading.value">SEARCH</button>
        </form>
        <p v-if="list.loading.value" role="status" class="dashboard-empty">Loading mobs…</p>
        <p v-else-if="list.error.value" role="alert" class="inline-notice inline-notice-error">{{ list.error.value }}</p>
        <template v-else-if="list.data.value">
          <p v-if="!list.data.value.results.length" class="dashboard-empty">
            {{ appliedQuery ? 'No mobs match this search.' : 'No mobs are loaded in this world.' }}
            <span v-if="list.data.value.world.lifecycle === 'stopped'">This world is stopped; enter it to load its mobs.</span>
          </p>
          <div v-else class="mob-zone-groups">
            <section v-for="(group, index) in zoneGroups" :key="group.key" class="mob-zone" :aria-labelledby="`mob-zone-${index}`">
              <div class="dashboard-section-heading">
                <h2 :id="`mob-zone-${index}`">{{ group.name }}</h2>
                <span class="tag" :aria-label="`${group.mobs.length} mobs on this page`">{{ group.mobs.length }}</span>
              </div>
              <ul class="mob-list">
                <li v-for="mob in group.mobs" :key="mob.id">
                  <router-link :to="`${listPath}/${mob.id}`" :aria-current="String(mob.id) === selectedId ? 'page' : undefined">
                    <strong>{{ mob.name }}</strong>
                    <span class="mob-list-summary" :title="`${mob.id} - ${mob.room.name}`">{{ mob.id }} - {{ mob.room.name }}</span>
                    <span v-if="mob.pending_deletion">Pending deletion</span>
                  </router-link>
                </li>
              </ul>
            </section>
          </div>
          <div class="dashboard-actions">
            <button v-if="cursors.length" class="btn-small button-gray" @click="previousPage">PREVIOUS</button>
            <button v-if="list.data.value.next_after" class="btn-small button-gray" @click="nextPage">NEXT 50</button>
          </div>
          <p class="dashboard-note">{{ list.data.value.results.length }} shown{{ cursors.length || list.data.value.next_after ? ' on this page' : '' }} · {{ timestamp(list.data.value.world.inspected_at) }}</p>
        </template>
      </section>
      <div class="mob-browser-detail dashboard-stack">
        <router-link v-if="selectedId" :to="listPath" class="mob-back dashboard-note">← Back to loaded mobs</router-link>
        <p v-if="detail.loading.value" class="dashboard-empty" role="status">Loading mob data…</p>
        <p v-else-if="detail.error.value" role="alert" class="inline-notice inline-notice-error">{{ detail.error.value }}</p>
        <RuntimeMobDetails v-else-if="detail.data.value" :mob="detail.data.value.mob" :staff="isStaff" :path="`${listPath}/${selectedId}`" @inventory-next="loadDetail" />
        <p v-else class="dashboard-empty">Choose a mob to see its location, stats, behavior, and conversation data.</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useStore } from 'vuex';
import { RefreshCw } from '@lucide/vue';
import RuntimeMobDetails from '@/components/builder/RuntimeMobDetails.vue';
import { useInspectionRequest } from '@/composables/useInspectionRequest';
const route = useRoute();
const store = useStore();
const adminPath = computed(() => `/build/worlds/${route.params.world_id}/admin/${route.params.instance_id}`);
const listPath = computed(() => `${adminPath.value}/mobs`);
const api = computed(() => `/builder/worlds/${route.params.world_id}/admin/instance/${route.params.instance_id}/mobs/`);
const selectedId = computed(() => String(route.params.mob_id || ''));
const isStaff = computed(() => !!store.state.auth?.user?.is_staff);
const list = useInspectionRequest();
const detail = useInspectionRequest();
type ListedMob = { id: number; name: string; pending_deletion: boolean; room: { name: string; zone: string; zone_id?: number } };
const zoneGroups = computed(() => {
  const groups = new Map<string | number, { key: string | number; name: string; mobs: ListedMob[] }>();
  for (const mob of (list.data.value?.results || []) as ListedMob[]) {
    const name = mob.room.zone || 'Unknown zone';
    const key = mob.room.zone_id ?? name;
    if (!groups.has(key)) groups.set(key, { key, name, mobs: [] });
    groups.get(key)!.mobs.push(mob);
  }
  return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name));
});
const query = ref('');
const appliedQuery = ref('');
const after = ref(0);
const cursors = ref<number[]>([]);
function loadList() { return list.load(api.value, { params: { q: appliedQuery.value, after: after.value } }); }
function loadDetail(inventoryAfter = 0) {
  if (!selectedId.value) { detail.clear(); return; }
  return detail.load(`${api.value}${selectedId.value}/`, { params: { inventory_after: inventoryAfter } });
}
function search() { after.value = 0; cursors.value = []; appliedQuery.value = query.value; void loadList(); }
function nextPage() { const next = list.data.value?.next_after; if (next) { cursors.value.push(after.value); after.value = next; void loadList(); } }
function previousPage() { after.value = cursors.value.pop() || 0; void loadList(); }
function refresh() { void loadList(); void loadDetail(); }
function timestamp(value: string) { return new Date(value).toLocaleTimeString(); }
watch(api, () => { query.value = ''; appliedQuery.value = ''; after.value = 0; cursors.value = []; void loadList(); }, { immediate: true });
watch(() => `${api.value}${selectedId.value}`, () => { void loadDetail(); }, { immediate: true });
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';
@import '@/styles/layout.scss';
.mob-browser { display: grid; grid-template-columns: minmax(210px, 1fr) minmax(0, 2fr); gap: 1.25rem; align-items: start; }
.mob-browser-list { min-width: 0; }
.mob-search { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 1rem; .form-group { width: 100%; min-width: 0; margin: 0; } }
.mob-zone {
  margin-top: 1.5rem;
  .dashboard-section-heading { align-items: center; margin-bottom: 0.25rem; }
  h2 { font-size: 14px; overflow-wrap: anywhere; min-width: 0; }
  .tag { flex-shrink: 0; }
}
.mob-list {
  margin: 0 0 1rem;
  padding: 0;
  list-style: none;

  a {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    padding: 0.85rem 0.5rem;
    border-bottom: 1px solid $color-background-border;
    overflow-wrap: anywhere;
    color: $color-text;
    text-decoration: none;

    &:hover, &:active, &:focus-visible {
      background: $color-background-very-light;
      color: $color-text;
      text-decoration: none;
    }

    &[aria-current="page"] {
      background: $color-background-very-light;
      border-left: 2px solid $color-secondary;
    }
  }

  span { color: $color-text-hex-60; font-size: 11px; }
  .mob-list-summary { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
}
.mob-back { display: none; }
@media ($mobile-site) {
  .mob-browser { display: block; }
  .mob-browser:not(.has-selection) .mob-browser-detail, .mob-browser.has-selection .mob-browser-list { display: none; }
  .mob-back { display: block; }
}
</style>
