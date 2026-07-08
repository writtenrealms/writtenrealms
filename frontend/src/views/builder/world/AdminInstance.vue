<template>
  <div v-if="instance" class="admin-instance">
    <div class="header-row">
      <div>
        <h2>{{ root_world.name.toUpperCase() }} WORLD #{{ instance.id }}</h2>
        <div class="header-meta">
          <span class="status-pill" :class="statusClass(instance.lifecycle_details.current)">
            {{ instance.lifecycle_details.current }}
          </span>
          <span class="color-text-50">{{ worldModeLabel }}</span>
          <span v-if="instance.parent_world" class="color-text-50">
            Base {{ instance.parent_world.name }} #{{ instance.parent_world.id }}
          </span>
        </div>
      </div>

      <div class="header-actions">
        <button
          class="btn btn-small"
          :disabled="isRefreshing"
          @click="refreshInstance"
        >
          {{ isRefreshing ? "REFRESHING..." : "REFRESH" }}
        </button>
        <button
          v-if="canResetWorld"
          class="btn btn-small"
          :disabled="isResetting"
          @click="onResetWorld"
        >
          {{ isResetting ? "RESETTING..." : "RESET WORLD" }}
        </button>
        <button
          v-if="canRecoverWorld"
          class="btn btn-small"
          :disabled="isRecovering"
          @click="onRecoverWorld"
        >
          {{ isRecovering ? "RECOVERING..." : "RECOVER" }}
        </button>
      </div>
    </div>

    <div v-if="canResetWorld" class="reset-note color-text-60">
      Reset clears transient runtime state for this stopped world: mobs, ground items, and other cleanup-safe spawn data.
    </div>

    <div class="summary-grid">
      <div v-for="card in summaryCards" :key="card.label" class="panel summary-card">
        <div class="summary-label color-text-50">{{ card.label }}</div>
        <div class="summary-value">{{ card.value }}</div>
        <div v-if="card.note" class="summary-note color-text-60">{{ card.note }}</div>
      </div>
    </div>

    <div class="details-grid">
      <section class="panel detail-panel">
        <h3 class="mb-3">Runtime Timeline</h3>

        <div class="detail-row">
          <span class="color-text-50">Lifecycle changed</span>
          <span>{{ formatTimestamp(instance.lifecycle_details.changed_at) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Last spawn-plan run</span>
          <span>{{ formatTimestamp(instance.lifecycle_details.last_spawn_plan_run_ts) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Last extraction</span>
          <span>{{ formatTimestamp(instance.lifecycle_details.last_extraction_ts) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Last entered</span>
          <span>{{ formatTimestamp(instance.lifecycle_details.last_entered_ts) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Last played</span>
          <span>{{ formatTimestamp(instance.lifecycle_details.last_played_ts) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Cleanup in progress</span>
          <span>
            <template v-if="instance.lifecycle_details.cleanup_started_ts">
              {{ formatTimestamp(instance.lifecycle_details.cleanup_started_ts) }}
            </template>
            <template v-else>No</template>
          </span>
        </div>
      </section>

      <section class="panel detail-panel">
        <h3 class="mb-3">World Details</h3>

        <div class="detail-row">
          <span class="color-text-50">World key</span>
          <span>{{ instance.key }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Mode</span>
          <span>{{ worldModeLabel }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Instance ref</span>
          <span>{{ instance.instance_ref || "None" }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Leader</span>
          <span>{{ instance.leader ? instance.leader.name : "None" }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Instance assignments</span>
          <span>{{ formatNumber(instance.counts.instance_assignments) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Player records</span>
          <span>{{ formatNumber(instance.counts.player_records) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Configured spawn plans</span>
          <span>{{ formatNumber(instance.spawn_plan_details.configured_spawn_plan_count) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Configured spawn entries</span>
          <span>{{ formatNumber(instance.spawn_plan_details.configured_spawn_entry_count) }}</span>
        </div>
      </section>

      <section class="panel detail-panel">
        <h3 class="mb-3">Item Distribution</h3>

        <div class="detail-row">
          <span class="color-text-50">On the ground</span>
          <span>{{ formatNumber(instance.counts.items_by_container_type.rooms) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Carried by players</span>
          <span>{{ formatNumber(instance.counts.items_by_container_type.players) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Held by mobs</span>
          <span>{{ formatNumber(instance.counts.items_by_container_type.mobs) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Inside other items</span>
          <span>{{ formatNumber(instance.counts.items_by_container_type.inside_items) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Equipped</span>
          <span>{{ formatNumber(instance.counts.items_by_container_type.equipment) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Without container</span>
          <span>{{ formatNumber(instance.counts.items_by_container_type.without_container) }}</span>
        </div>
        <div class="detail-row">
          <span class="color-text-50">Pending deletion</span>
          <span>{{ formatNumber(instance.counts.items_pending_deletion) }}</span>
        </div>
      </section>

      <section class="panel detail-panel players-panel">
        <div class="section-heading">
          <h3>Logged In Players</h3>
          <div class="color-text-50">{{ formatNumber(instance.counts.players_logged_in) }} active</div>
        </div>

        <div v-if="!instance.active_players.length" class="empty-state color-text-60">
          No players are currently logged into this world.
        </div>

        <div v-else class="player-list">
          <router-link
            v-for="player in instance.active_players"
            :key="player.id"
            class="player-card"
            :to="playerLink(player)"
          >
            <div class="player-topline">
              <div class="player-name">{{ player.name }}</div>
              <div v-if="player.is_builder" class="player-flag">Builder</div>
            </div>

            <div class="color-text-60">
              {{ player.room ? player.room.name : "No room" }}
            </div>
            <div class="player-times color-text-50">
              <div>Connected {{ formatRelativeTime(player.last_connection_ts) }}</div>
              <div>Active {{ formatRelativeTime(player.last_action_ts) }}</div>
            </div>
          </router-link>
        </div>
      </section>

      <section class="panel detail-panel state-panel">
        <div class="section-heading">
          <h3>Spawn World State</h3>
          <div class="color-text-50">{{ formatNumber(stateEntries.length) }} keys</div>
        </div>

        <div class="color-text-60 section-note">
          This is the current `world`-scope state snapshot for spawn world #{{ instance.id }}, not the template world.
        </div>

        <div v-if="!stateEntries.length" class="empty-state color-text-60">
          No world state is currently set on this spawn world.
        </div>

        <div v-else class="state-list">
          <div v-for="entry in stateEntries" :key="entry.key" class="state-row">
            <div class="state-key">{{ entry.key }}</div>
            <div class="state-value">{{ formatStateValue(entry.value) }}</div>
          </div>
        </div>
      </section>
    </div>
  </div>

  <div v-else class="color-text-60">
    Loading world data...
  </div>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useStore } from 'vuex';
import { useRoute } from 'vue-router';

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
  if (!status) {
    return '';
  }
  return `status-${status}`;
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
@import "@/styles/colors.scss";

.admin-instance {
  max-width: 1200px;
}

.eyebrow {
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.header-actions {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}

.header-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  margin-top: 12px;
}

.back-link {
  color: $color-secondary;
  text-decoration: none;
  padding-top: 6px;
}

.reset-note {
  margin: -8px 0 20px;
  max-width: 720px;
}

.summary-grid,
.details-grid {
  display: grid;
  gap: 16px;
}

.summary-grid {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  margin-bottom: 16px;
}

.details-grid {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.summary-card,
.detail-panel {
  border: 1px solid $color-background-light-border;
  background:
    linear-gradient(180deg, rgba($color-primary, 0.1), rgba($color-background-light, 0.35)),
    $color-background-light;
}

.summary-card {
  min-height: 120px;
}

.summary-label {
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.summary-value {
  font-size: 30px;
  font-weight: 600;
  line-height: 1.05;
  margin-top: 10px;
}

.summary-note {
  margin-top: 10px;
  font-size: 13px;
}

.detail-panel {
  padding: 18px 20px;
}

.section-note {
  margin: -4px 0 16px;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 0;
  border-top: 1px solid rgba($color-background-light-border-selected, 0.18);
}

.detail-row:first-of-type {
  border-top: 0;
  padding-top: 0;
}

.section-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: baseline;
  margin-bottom: 16px;
}

.state-panel {
  grid-column: span 2;
}

.state-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.state-row {
  display: grid;
  grid-template-columns: minmax(180px, 240px) minmax(0, 1fr);
  gap: 16px;
  padding: 11px 0;
  border-top: 1px solid rgba($color-background-light-border-selected, 0.18);
}

.state-row:first-of-type {
  border-top: 0;
  padding-top: 0;
}

.state-key {
  font-weight: 600;
  word-break: break-word;
}

.state-value {
  color: $color-text-70;
  white-space: pre-wrap;
  word-break: break-word;
}

.player-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.player-card {
  display: block;
  padding: 14px;
  border: 1px solid $color-background-light-border;
  background: rgba($color-background-black, 0.55);
  text-decoration: none;
  color: $color-text;
  transition: border-color 0.12s ease, transform 0.12s ease;
}

.player-card:hover {
  border-color: rgba($color-secondary, 0.55);
  transform: translateY(-1px);
}

.player-topline {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 6px;
}

.player-name {
  font-size: 16px;
  font-weight: 600;
}

.player-flag,
.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.player-flag {
  color: $color-secondary;
  background: rgba($color-secondary, 0.12);
  border: 1px solid rgba($color-secondary, 0.3);
}

.status-pill {
  border: 1px solid $color-background-light-border-selected;
  background: rgba($color-background-light-border-selected, 0.15);
}

.status-running {
  color: $color-green;
  border-color: rgba($color-green, 0.35);
  background: rgba($color-green, 0.14);
}

.status-starting,
.status-stopping {
  color: $color-secondary;
  border-color: rgba($color-secondary, 0.35);
  background: rgba($color-secondary, 0.14);
}

.status-stopped,
.status-stored,
.status-new,
.status-clean {
  color: $color-text-70;
}

.status-killed,
.status-error,
.status-failed {
  color: $color-red;
  border-color: rgba($color-red, 0.35);
  background: rgba($color-red, 0.14);
}

.player-times {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 10px;
  font-size: 12px;
}

.empty-state {
  padding: 18px 0 6px;
}

@media (max-width: 720px) {
  .summary-value {
    font-size: 26px;
  }

  .detail-row {
    flex-direction: column;
    gap: 4px;
  }

  .state-panel {
    grid-column: auto;
  }

  .state-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }
}
</style>
