<template>
  <div v-if="instance" class="admin-instance dashboard-view">
    <header class="dashboard-heading">
      <div>
        <div class="dashboard-eyebrow"><Shield :size="15" aria-hidden="true" /> WORLD ADMINISTRATION</div>
        <h1>{{ root_world.name }} world #{{ instance.id }}</h1>
        <div class="dashboard-actions dashboard-note">
          <span class="tag" :class="statusClass(instance.lifecycle_details.current)">
            {{ instance.lifecycle_details.current }}
          </span>
          <span>{{ worldModeLabel }}</span>
          <span v-if="instance.parent_world" class="color-text-60">
            Base {{ instance.parent_world.name }} #{{ instance.parent_world.id }}
          </span>
        </div>
      </div>

      <div class="dashboard-actions">
        <button
          class="btn-small button-gray"
          :disabled="isRefreshing"
          @click="refreshInstance"
        >
          <RefreshCw :size="13" aria-hidden="true" /> {{ isRefreshing ? "REFRESHING…" : "REFRESH" }}
        </button>
        <button
          v-if="canResetWorld"
          class="btn-small"
          :disabled="isResetting"
          @click="onResetWorld"
        >
          {{ isResetting ? "RESETTING..." : "RESET WORLD" }}
        </button>
        <button
          v-if="canRecoverWorld"
          class="btn-small"
          :disabled="isRecovering"
          @click="onRecoverWorld"
        >
          {{ isRecovering ? "RECOVERING..." : "RECOVER" }}
        </button>
      </div>
    </header>

    <div class="dashboard-stack">
      <div v-if="canResetWorld" class="inline-notice">
        Reset clears transient runtime state for this stopped world: mobs, ground items, and other cleanup-safe spawn data.
      </div>

      <div class="metric-grid">
        <component :is="card.to ? 'router-link' : 'div'" v-for="card in summaryCards" :key="card.label" :to="card.to" class="metric-card" :class="{ 'metric-card-link': card.to }">
          <span>{{ card.label }} <ArrowRight v-if="card.to" :size="14" aria-hidden="true" /></span>
          <strong :class="{ 'metric-value-text': card.textValue }">{{ card.value }}</strong>
          <small v-if="card.note">{{ card.note }}</small>
        </component>
      </div>

      <div class="dashboard-grid">
        <section class="panel dashboard-section">
          <div class="dashboard-section-heading"><h2>Runtime timeline</h2></div>
          <dl class="dashboard-facts">
            <div>
              <dt>Lifecycle changed</dt>
              <dd>{{ formatTimestamp(instance.lifecycle_details.changed_at) }}</dd>
            </div>
            <div>
              <dt>Last spawn-plan run</dt>
              <dd>{{ formatTimestamp(instance.lifecycle_details.last_spawn_plan_run_ts) }}</dd>
            </div>
            <div>
              <dt>Last extraction</dt>
              <dd>{{ formatTimestamp(instance.lifecycle_details.last_extraction_ts) }}</dd>
            </div>
            <div>
              <dt>Last entered</dt>
              <dd>{{ formatTimestamp(instance.lifecycle_details.last_entered_ts) }}</dd>
            </div>
            <div>
              <dt>Last played</dt>
              <dd>{{ formatTimestamp(instance.lifecycle_details.last_played_ts) }}</dd>
            </div>
            <div>
              <dt>Cleanup in progress</dt>
              <dd>
                <template v-if="instance.lifecycle_details.cleanup_started_ts">
                  {{ formatTimestamp(instance.lifecycle_details.cleanup_started_ts) }}
                </template>
                <template v-else>No</template>
              </dd>
            </div>
          </dl>
        </section>

        <section class="panel dashboard-section">
          <div class="dashboard-section-heading"><h2>World details</h2></div>
          <dl class="dashboard-facts">
            <div>
              <dt>World key</dt>
              <dd>{{ instance.key }}</dd>
            </div>
            <div>
              <dt>Mode</dt>
              <dd>{{ worldModeLabel }}</dd>
            </div>
            <div>
              <dt>Instance ref</dt>
              <dd>{{ instance.instance_ref || "None" }}</dd>
            </div>
            <div>
              <dt>Leader</dt>
              <dd>{{ instance.leader ? instance.leader.name : "None" }}</dd>
            </div>
            <div>
              <dt>Instance assignments</dt>
              <dd>{{ formatNumber(instance.counts.instance_assignments) }}</dd>
            </div>
            <div>
              <dt>Player records</dt>
              <dd>{{ formatNumber(instance.counts.player_records) }}</dd>
            </div>
            <div>
              <dt>Configured spawn plans</dt>
              <dd>{{ formatNumber(instance.spawn_plan_details.configured_spawn_plan_count) }}</dd>
            </div>
            <div>
              <dt>Configured spawn entries</dt>
              <dd>{{ formatNumber(instance.spawn_plan_details.configured_spawn_entry_count) }}</dd>
            </div>
          </dl>
        </section>

        <section class="panel dashboard-section">
          <div class="dashboard-section-heading"><h2>Item distribution</h2></div>
          <dl class="dashboard-facts">
            <div>
              <dt>On the ground</dt>
              <dd>{{ formatNumber(instance.counts.items_by_container_type.rooms) }}</dd>
            </div>
            <div>
              <dt>Carried by players</dt>
              <dd>{{ formatNumber(instance.counts.items_by_container_type.players) }}</dd>
            </div>
            <div>
              <dt>Held by mobs</dt>
              <dd>{{ formatNumber(instance.counts.items_by_container_type.mobs) }}</dd>
            </div>
            <div>
              <dt>Inside other items</dt>
              <dd>{{ formatNumber(instance.counts.items_by_container_type.inside_items) }}</dd>
            </div>
            <div>
              <dt>Equipped</dt>
              <dd>{{ formatNumber(instance.counts.items_by_container_type.equipment) }}</dd>
            </div>
            <div>
              <dt>Without container</dt>
              <dd>{{ formatNumber(instance.counts.items_by_container_type.without_container) }}</dd>
            </div>
            <div>
              <dt>Pending deletion</dt>
              <dd>{{ formatNumber(instance.counts.items_pending_deletion) }}</dd>
            </div>
          </dl>
        </section>

      </div>

      <section class="panel dashboard-section" aria-labelledby="world-players-heading">
        <div class="dashboard-section-heading">
          <h2 id="world-players-heading">Logged in players</h2>
          <span class="tag">{{ formatNumber(instance.counts.players_logged_in) }} active</span>
        </div>
        <p v-if="!instance.active_players.length" class="dashboard-empty">
          No players are currently logged into this world.
        </p>
        <div v-else class="data-table-scroll" tabindex="0" role="region" aria-label="Logged in players table">
          <table class="data-table dashboard-table">
            <thead><tr><th scope="col">Character</th><th scope="col">Room</th><th scope="col">Connected</th><th scope="col">Last active</th></tr></thead>
            <tbody>
              <tr v-for="player in instance.active_players" :key="player.id">
                <th scope="row"><router-link :to="playerLink(player)">{{ player.name }}</router-link> <span v-if="player.is_builder" class="tag tag-secondary">Builder</span></th>
                <td>{{ player.room ? player.room.name : "No room" }}</td>
                <td>{{ formatRelativeTime(player.last_connection_ts) }}</td>
                <td>{{ formatRelativeTime(player.last_action_ts) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel dashboard-section" aria-labelledby="world-state-heading">
        <div class="dashboard-section-heading">
          <div>
            <h2 id="world-state-heading">Spawn world state</h2>
            <p>Current world-scope state for spawn world #{{ instance.id }}.</p>
          </div>
          <span class="tag">{{ formatNumber(stateEntries.length) }} keys</span>
        </div>
        <p v-if="!stateEntries.length" class="dashboard-empty">No world state is currently set on this spawn world.</p>
        <dl v-else class="dashboard-facts world-state">
          <div v-for="entry in stateEntries" :key="entry.key">
            <dt>{{ entry.key }}</dt>
            <dd class="state-value">{{ formatStateValue(entry.value) }}</dd>
          </div>
        </dl>
      </section>
    </div>
  </div>

  <div v-else class="color-text-60" role="status">
    Loading world data...
  </div>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useStore } from 'vuex';
import { useRoute } from 'vue-router';
import { ArrowRight, RefreshCw, Shield } from '@lucide/vue';

const store = useStore();
const route = useRoute();
const isResetting = ref(false);
const isRecovering = ref(false);
const isRefreshing = ref(false);
let refreshTimer: number | null = null;

const root_world: any = computed(() => store.state.builder.world);
const instance = computed<any>(() => store.state.builder.worlds.admin.admin_instance);

const fetchInstance = async ({ preserveCurrent = false } = {}) => {
  return store.dispatch('builder/worlds/admin/world_admin_instance_fetch', {
    world_id: route.params.world_id,
    instance_id: route.params.instance_id,
    preserveCurrent,
  });
};

watch(
  () => [route.params.world_id, route.params.instance_id],
  () => {
    fetchInstance();
  },
  { immediate: true },
);

const shouldAutoRefresh = computed(() => {
  const lifecycle = instance.value?.lifecycle_details?.current;
  return ['running', 'starting', 'stopping', 'restarting', 'queued', 'stored'].includes(lifecycle);
});

const worldModeLabel = computed(() => {
  if (!instance.value) {
    return '';
  }
  return instance.value.is_multiplayer ? 'Multiplayer spawn' : 'Single-player spawn';
});

const canResetWorld = computed(() => {
  return instance.value?.lifecycle_details?.current === 'stopped';
});

const canRecoverWorld = computed(() => {
  return Boolean(instance.value?.recovery_actions?.recover_to_stopped);
});

const stateEntries = computed(() => {
  const worldState = instance.value?.world_state;
  if (!worldState || typeof worldState !== 'object') {
    return [];
  }

  return Object.keys(worldState)
    .sort((left, right) => left.localeCompare(right))
    .map((key) => ({
      key,
      value: worldState[key],
    }));
});

const summaryCards = computed(() => {
  if (!instance.value) {
    return [];
  }

  return [
    {
      label: 'Mobs loaded',
      to: `/build/worlds/${route.params.world_id}/admin/${route.params.instance_id}/mobs`,
      value: formatNumber(instance.value.counts.mobs_loaded),
      note: `${formatNumber(instance.value.counts.mobs_pending_deletion)} pending deletion`,
    },
    {
      label: 'Items on ground',
      value: formatNumber(instance.value.counts.items_on_ground),
      note: `${formatNumber(instance.value.counts.items_by_container_type.inside_items)} nested in containers`,
    },
    {
      label: 'Items total',
      value: formatNumber(instance.value.counts.items_total),
      note: `${formatNumber(instance.value.counts.items_pending_deletion)} pending deletion`,
    },
    {
      label: 'Players logged in',
      value: formatNumber(instance.value.counts.players_logged_in),
      note: `${formatNumber(instance.value.counts.player_records)} player records in world`,
    },
    {
      label: 'Last spawn-plan run',
      textValue: true,
      value: formatRelativeTime(instance.value.spawn_plan_details.last_run_ts),
      note: formatTimestamp(instance.value.spawn_plan_details.last_run_ts),
    },
    {
      label: 'Assignments',
      value: formatNumber(instance.value.counts.instance_assignments),
      note: instance.value.leader ? `Leader ${instance.value.leader.name}` : 'No leader assigned',
    },
  ];
});

const playerLink = (player: any) => {
  return {
    name: 'builder_world_player_details',
    params: {
      world_id: route.params.world_id,
      player_id: player.id,
    },
  };
};

const refreshInstance = async () => {
  if (isRefreshing.value || isResetting.value || isRecovering.value) {
    return;
  }

  isRefreshing.value = true;
  try {
    await fetchInstance({ preserveCurrent: true });
  } finally {
    isRefreshing.value = false;
  }
};

const refreshIfVisible = async () => {
  if (document.visibilityState !== 'visible' || !shouldAutoRefresh.value) {
    return;
  }
  await refreshInstance();
};

const onWindowFocus = async () => {
  await refreshInstance();
};

onMounted(() => {
  window.addEventListener('focus', onWindowFocus);
  refreshTimer = window.setInterval(() => {
    refreshIfVisible();
  }, 5000);
});

onBeforeUnmount(() => {
  window.removeEventListener('focus', onWindowFocus);
  if (refreshTimer !== null) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
});

const onResetWorld = async () => {
  if (!instance.value || !canResetWorld.value || isResetting.value || isRecovering.value) {
    return;
  }

  const confirmed = confirm(
    `Reset world ${instance.value.id}? This will remove mobs and ground-loaded runtime state for this stopped world.`
  );
  if (!confirmed) {
    return;
  }

  isResetting.value = true;
  store.commit('ui/notification_set', {
    text: 'Resetting stopped world...',
    expires: false,
  });

  try {
    await store.dispatch('builder/worlds/admin/world_admin_instance_reset', {
      world_id: route.params.world_id,
      instance_id: route.params.instance_id,
    });
    store.commit('ui/notification_set', 'World reset.', { root: true });
  } finally {
    isResetting.value = false;
  }
};

const onRecoverWorld = async () => {
  if (!instance.value || !canRecoverWorld.value || isRecovering.value || isResetting.value) {
    return;
  }

  const confirmed = confirm(
    `Recover world ${instance.value.id}? This will move it to stopped and clean transient runtime state.`
  );
  if (!confirmed) {
    return;
  }

  isRecovering.value = true;
  store.commit('ui/notification_set', {
    text: 'Recovering world...',
    expires: false,
  });

  try {
    await store.dispatch('builder/worlds/admin/world_admin_instance_recover', {
      world_id: route.params.world_id,
      instance_id: route.params.instance_id,
    });
    store.commit('ui/notification_set', 'World recovered.', { root: true });
  } finally {
    isRecovering.value = false;
  }
};

const statusClass = (status: string) => {
  if (status === 'running') return 'tag-green';
  if (['starting', 'stopping', 'restarting', 'queued'].includes(status)) return 'tag-secondary';
  if (['killed', 'error', 'failed'].includes(status)) return 'tag-red';
  return '';
};

const formatNumber = (value: number | string | null | undefined) => {
  return Number(value || 0).toLocaleString();
};

const formatStateValue = (value: unknown) => {
  if (value === null) {
    return 'null';
  }
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const formatTimestamp = (value: string | null | undefined) => {
  if (!value) {
    return 'Never';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString();
};

const formatRelativeTime = (value: string | null | undefined) => {
  if (!value) {
    return 'Never';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  const diffMs = Date.now() - parsed.getTime();
  const absSeconds = Math.max(Math.round(Math.abs(diffMs) / 1000), 0);

  if (absSeconds < 60) {
    return diffMs >= 0 ? 'just now' : 'in moments';
  }

  const units = [
    { limit: 3600, size: 60, label: 'minute' },
    { limit: 86400, size: 3600, label: 'hour' },
    { limit: 604800, size: 86400, label: 'day' },
  ];

  for (const unit of units) {
    if (absSeconds < unit.limit) {
      const amount = Math.round(absSeconds / unit.size);
      const suffix = amount === 1 ? unit.label : `${unit.label}s`;
      return diffMs >= 0 ? `${amount} ${suffix} ago` : `in ${amount} ${suffix}`;
    }
  }

  const weeks = Math.round(absSeconds / 604800);
  return diffMs >= 0 ? `${weeks} week${weeks === 1 ? '' : 's'} ago` : `in ${weeks} week${weeks === 1 ? '' : 's'}`;
};
</script>

<style lang="scss" scoped>
@import "@/styles/layout.scss";

.admin-instance {
  width: 100%;

  .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .world-state > div { grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); }
  .state-value { white-space: pre-wrap; text-align: left; }

  @media ($mobile-site) {
    .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
}
</style>
