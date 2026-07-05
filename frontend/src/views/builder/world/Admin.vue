<template>
  <div v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <div v-if="world_admin.id">
      <h2>{{ root_world.name.toUpperCase() }} ADMIN</h2>
      <div class="world-status">

        <div class="color-text-50">
          <span v-if="world_admin.is_multiplayer">Multiplayer</span>
          <span v-else>Single Player</span>
          World
        </div>

        <!-- Maintenance Box -->
        <div class="maintenance panel mt-4">
          <div class="maintenance-status mb-2">
            Maintenance mode:
            <span v-if="root_world.maintenance_mode">ON</span>
            <span v-else>OFF</span>
            <Help help="Players cannot enter a world in maintenance (but builders can)."/>
          </div>

          <div class="form-group">
            <input type="text" placeholder="Maintenance Message" v-model="maintenance_msg">
          </div>

          <div class="slider-container">
            <Slider
              :value="root_world.maintenance_mode"
              @change="onSliderChange"/>
            </div>
          </div>
        </div>

        <div class="admin-section mt-8">
          <div class="section-heading">
            <h3>SPAWNED WORLDS</h3>
            <div class="color-text-50">{{ spawnedWorlds.length }} total</div>
          </div>

          <div v-if="spawnedWorlds.length" class="admin-list">
            <div v-for="spawn_world in spawnedWorlds" v-bind:key="spawn_world.id" class="admin-row">
              <div class="admin-row-main">
                <div>
                  <router-link :to="admin_instance_link(spawn_world.id)">#{{ spawn_world.id }}</router-link>
                  - {{ spawn_world.name }}
                  <span class="color-text-50 ml-2">[ {{ spawn_world.lifecycle }} ]</span>
                </div>
                <div class="meta-row color-text-60">
                  {{ spawn_world.forge_data.num_players }} players,
                  {{ spawn_world.forge_data.num_mobs }} mobs,
                  {{ spawn_world.forge_data.num_items }} items
                </div>
              </div>
              <div class="actions">
                <button class="btn btn-small start" :disabled="disableStart(spawn_world)" @click="onStart(spawn_world)">START</button>
                <button class="btn btn-small stop ml-2" :disabled="disableStop(spawn_world)" @click="onStop(spawn_world)">STOP</button>
                <button
                  v-if="canRecover(spawn_world)"
                  class="btn btn-small ml-2"
                  :disabled="action_submitted[spawn_world.id]"
                  @click="onRecover(spawn_world)"
                >
                  RECOVER
                </button>
              </div>
            </div>
          </div>
          <div v-else class="empty-state color-text-60">
            No spawned worlds for this world.
          </div>
        </div>

        <div class="admin-section mt-8">
          <div class="section-heading">
            <h3>SPAWNED INSTANCES</h3>
            <div class="color-text-50">{{ instanceRuns.length }} total</div>
          </div>

          <div v-if="instanceRuns.length" class="admin-list">
            <div v-for="run in instanceRuns" v-bind:key="run.id" class="admin-row">
              <div class="admin-row-main">
                <div>
                  <router-link :to="admin_instance_link(run.spawned_world.id)">
                    #{{ run.spawned_world.id }}
                  </router-link>
                  - {{ run.template_world.name }}
                  <span class="color-text-50 ml-2">[ {{ run.status }} / {{ run.spawned_world.lifecycle }} ]</span>
                </div>
                <div class="meta-row color-text-60">
                  Ref {{ run.ref || "none" }},
                  {{ run.active_participant_count }}/{{ run.participant_count }} active participants,
                  last active {{ formatTimestamp(run.last_active_at) }}
                </div>
              </div>
              <div class="actions">
                <button class="btn btn-small start" :disabled="disableStart(run.spawned_world)" @click="onStart(run.spawned_world)">START</button>
                <button class="btn btn-small stop ml-2" :disabled="disableStop(run.spawned_world)" @click="onStop(run.spawned_world)">STOP</button>
                <button
                  v-if="canRecover(run.spawned_world)"
                  class="btn btn-small ml-2"
                  :disabled="action_submitted[run.spawned_world.id]"
                  @click="onRecover(run.spawned_world)"
                >
                  RECOVER
                </button>
              </div>
            </div>
          </div>
          <div v-else class="empty-state color-text-60">
            No spawned instances for this world.
          </div>
        </div>

        <!-- Stats -->
        <div v-if="world_admin && world_admin.stats" class="mt-8">
          <h3 class="mb-2">STATS</h3>
          <div>Rooms: {{ world_admin.stats.num_rooms }}</div>
          <div>Mob Templates: {{ world_admin.stats.num_mob_templates }}</div>
          <div>Item Templates: {{ world_admin.stats.num_item_templates }}</div>
        </div>
    </div>
  </div>
  <div v-else>
    You do not have permission to administrate this world.
  </div>
</template>

<script lang='ts' setup>
import { computed, ref, onMounted, onUnmounted } from 'vue';
import { useStore } from 'vuex';
import { useRoute } from 'vue-router';
import Slider from "@/components/forms/Slider.vue";
import Help from "@/components/Help.vue";

const store = useStore();
const route = useRoute();

// Index of which worlds have an action that was just fired off, so that we
// can disable the other actions in order not to spam the server.
const action_submitted = ref<Record<number, boolean>>({});
const maintenance_msg = ref('');

const root_world = computed<any>(() => store.state.builder.world);
const world_admin = computed<any>(() => store.state.builder.worlds.admin.world_admin);
const spawnedWorlds = computed<any[]>(() => world_admin.value?.spawned_worlds || []);
const instanceRuns = computed<any[]>(() => world_admin.value?.instance_runs || []);
const recoverableLifecycles = ['starting', 'stopping', 'restarting', 'queued', 'stored'];

onMounted(async () => {
  maintenance_msg.value = root_world.value.maintenance_msg || '';

  await store.dispatch('forge/send', {
    'type': 'sub',
    'sub': 'builder.admin',
    'world_id': root_world.value.id,
  });

  await store.dispatch(
    'builder/worlds/admin/world_admin_fetch',
    route.params.world_id);

});

onUnmounted(async () => {
  await store.dispatch('forge/send', {
    'type': 'unsub',
    'sub': 'builder.admin',
    'world_id': route.params.world_id,
  })
});

const admin_instance_link = (instance_id: number | string) => {
  return {
    name: 'builder_world_admin_instance',
    params: {
      world_id: route.params.world_id,
      instance_id: instance_id,
    }
  }
};

const disableStart = (instance: any) => {
  if (action_submitted.value[instance.id]) return true;
  if (instance.lifecycle == 'stopped' || instance.lifecycle == 'new') return false;
  return true;
};

const disableStop = (instance: any) => {
  if (action_submitted.value[instance.id]) return true;
  if (instance.lifecycle == 'running') return false;
  return true;
};

const canRecover = (instance: any) => {
  if (!instance) return false;
  if (instance.recovery_actions?.recover_to_stopped) return true;
  return recoverableLifecycles.includes(instance.lifecycle);
};

const onStart = async (instance: any) => {
  action_submitted.value[instance.id] = true;
  store.commit('ui/notification_set', {
    text: "Starting world, this may take a minute...",
    expires: false
  });

  await store.dispatch('forge/send', {
    'type': 'job',
    'job': 'start_world',
    'world_id': instance.id,
  });
  action_submitted.value[instance.id] = false;
};

const onStop = async (instance: any) => {
  action_submitted.value[instance.id] = true;
  store.commit('ui/notification_set', {
    text: "Stopping world, this may take a minute...",
    expires: false
  });

  await store.dispatch('forge/send', {
    'type': 'job',
    'job': 'stop_world',
    'world_id': instance.id,
  });
  action_submitted.value[instance.id] = false;
};

const onRecover = async (instance: any) => {
  const confirmed = confirm(
    `Recover world ${instance.id}? This will move it to stopped and clean transient runtime state.`
  );
  if (!confirmed) {
    return;
  }

  action_submitted.value[instance.id] = true;
  store.commit('ui/notification_set', {
    text: "Recovering world...",
    expires: false
  });

  try {
    await store.dispatch('builder/worlds/admin/world_admin_instance_recover', {
      world_id: route.params.world_id,
      instance_id: instance.id,
    });
    store.commit('ui/notification_set', 'World recovered.', { root: true });
  } finally {
    action_submitted.value[instance.id] = false;
  }
};
const onSliderChange = async (newValue: boolean) => {
  await store.dispatch(
    'builder/world_patch',
    {
      maintenance_mode: newValue,
      maintenance_msg: maintenance_msg.value,
    });
};

const formatTimestamp = (value: string | null | undefined) => {
  if (!value) {
    return 'never';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString();
};

</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.actions > button[disabled] {
  color: $color-text-half;
  border-color: $color-text-half;
  cursor: not-allowed;
}

.admin-section {
  width: 100%;
  max-width: 920px;
}

.section-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
}

.section-heading h3 {
  margin: 0;
}

.admin-list {
  border-top: 1px solid $color-background-light-border;
}

.admin-row {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 12px 0;
  border-bottom: 1px solid $color-background-light-border;
}

.admin-row-main {
  min-width: 0;
}

.meta-row {
  margin-top: 4px;
  line-height: 1.4;
}

.empty-state {
  padding: 10px 0;
}

.world-status {
  width: 100%;
  .maintenance {
    width: 100%;
    max-width: 600px;
    .slider-container {
      transform: scale(0.8);
      transform-origin: top left;
    }
  }
}
</style>
