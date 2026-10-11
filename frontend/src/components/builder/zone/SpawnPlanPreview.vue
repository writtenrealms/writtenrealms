<template>
  <details class="spawn-plan-preview" v-on="{ toggle: onToggle }">
    <summary>
      {{ plan.name || plan.slug }}
      <span class="plan-summary color-text-50">
        {{ plan.num_entries }} {{ plan.num_entries === 1 ? 'entry' : 'entries' }}
        <span v-if="plan.respawn_mode">/ {{ plan.respawn_mode }}</span>
        <span v-if="plan.is_active === false"> · Inactive</span>
      </span>
    </summary>
    <div v-if="loading" class="preview-status color-text-60" role="status">Loading spawns…</div>
    <div v-else-if="error" class="preview-status" role="alert">
      {{ error }} <button class="btn-thin" @click="load">RETRY</button>
    </div>
    <template v-else-if="data">
      <p class="preview-status color-text-60">Planned spawns. Hover or tap a mob for details.</p>
      <ul v-if="data.entries.length" class="spawn-entries">
        <li v-for="entry in data.entries" :key="entry.slug">
          <div v-if="entry.pooled || !entry.is_active || entry.conditional" class="entry-heading">
            <span v-if="entry.pooled" class="color-text-70">{{ formatSpawnCount(entry.count) }} × Weighted choice</span>
            <span v-if="!entry.is_active" class="color-text-50">Inactive</span>
            <span v-if="entry.conditional" class="color-text-50">Conditional</span>
          </div>
          <div v-for="(source, index) in entry.sources" :key="index" class="spawn-source">
            <span v-if="!entry.pooled" class="color-text-70">{{ formatSpawnCount(entry.count) }} ×</span>
            <MobDefinitionPreview v-if="source.mob_id && data.mobs[source.mob_id]"
              :mob="data.mobs[source.mob_id]" :world-id="worldId" />
            <span v-else>{{ source.name || source.ref }}</span>
            <small v-if="entry.pooled" class="color-text-60">weight {{ source.weight }}</small>
          </div>
          <div class="spawn-target color-text-60">{{ entry.target_name }} · {{ entry.target }}</div>
        </li>
      </ul>
      <p v-else class="preview-status color-text-50">No spawn entries.</p>
      <router-link class="btn-thin" :to="{
        name: 'builder_zone_spawn_plan_details',
        params: { world_id: worldId, zone_relative_id: zoneRelativeId, spawn_plan_id: plan.id },
      }">EDIT YAML</router-link>
    </template>
  </details>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
import axios from 'axios';
import MobDefinitionPreview from '@/components/builder/world/MobDefinitionPreview.vue';
import { formatSpawnCount, type SpawnPlanPreviewData } from '@/core/spawnPlanPreview';

const props = defineProps<{
  plan: { id: number; name?: string; slug: string; num_entries: number; respawn_mode?: string; is_active?: boolean };
  worldId: string | number;
  zoneId: number;
  zoneRelativeId: string | number;
}>();
const data = ref<SpawnPlanPreviewData | null>(null);
const loading = ref(false);
const error = ref('');
const requestSource = axios.CancelToken.source();
const load = async () => {
  if (loading.value || data.value) return;
  loading.value = true;
  error.value = '';
  try {
    const response = await axios.get(
      `/builder/worlds/${props.worldId}/zones/${props.zoneId}/spawn-plans/${props.plan.id}/`,
      { params: { preview: 'true' }, cancelToken: requestSource.token },
    );
    data.value = response.data;
  } catch (err: any) {
    if (!axios.isCancel(err)) error.value = err?.response?.data?.detail || 'Could not load spawns.';
  } finally {
    loading.value = false;
  }
};
const onToggle = (event: Event) => {
  if ((event.target as HTMLDetailsElement).open) void load();
};
onBeforeUnmount(() => requestSource.cancel('Spawn preview was closed.'));
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';

.spawn-plan-preview {
  border-bottom: 1px solid $color-background-border;
  padding: 0.65rem 0;
  min-width: 0;
  overflow-wrap: anywhere;

  summary { cursor: pointer; }
  .plan-summary { display: block; margin: 0.2rem 0 0 1rem; }
  .preview-status { margin: 0.75rem 0; font-size: 0.85rem; }
  .spawn-entries { list-style: none; padding: 0; margin: 0; }
  li { border-top: 1px solid $color-background-very-light; padding: 0.75rem 0; }
  .entry-heading, .spawn-source { display: flex; align-items: baseline; flex-wrap: wrap; gap: 0.4rem; }
  .entry-heading, .spawn-target { font-size: 0.85rem; }
  .spawn-target { margin-top: 0.25rem; }
}
</style>
