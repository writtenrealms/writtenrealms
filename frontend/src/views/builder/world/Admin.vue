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

        <div>
          <h3 class="mt-8 mb-2">SPAWNED WORLDS</h3>
          <div v-for="spawn_world in world_admin.spawned_worlds" v-bind:key="spawn_world.id">
            <div class="mb-2">
              <router-link :to="admin_instance_link(spawn_world.id)">{{ spawn_world.id }}</router-link> - {{ spawn_world.name }} <span class="color-text-50 ml-2">[ {{ spawn_world.lifecycle }} ]</span>
            </div>
            <div class="actions">
              <button class="btn btn-small start" :disabled="disableStart(spawn_world)" @click="onStart(spawn_world)">START</button>
              <button class="btn btn-small stop ml-2" :disabled="disableStop(spawn_world)" @click="onStop(spawn_world)">STOP</button>
              <!-- <button class="btn btn-small kill ml-2" :disabled="disableKill(spawn_world)" @click="onKill(spawn_world)">KILL</button> -->
            </div>
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
const action_submitted = ref({});
const maintenance_msg = ref('');

const root_world: any = computed(() => store.state.builder.world);
const world_admin = computed(() => store.state.builder.worlds.admin.world_admin);

onMounted(async () => {
  maintenance_msg.value = root_world.maintenance_msg;

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

const admin_instance_link = (instance_id) => {
  return {
    name: 'builder_world_admin_instance',
    params: {
      world_id: route.params.world_id,
      instance_id: instance_id,
    }
  }
};

const disableStart = (instance) => {
  if (instance.lifecycle == 'stopped' || instance.lifecycle == 'new') return false;
  return true;
};

const disableStop = (instance) => {
  if (instance.lifecycle == 'running') return false;
  return true;
};

const onStart = async (instance) => {
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

const onStop = async (instance) => {
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
const onSliderChange = async (newValue: boolean) => {
  await store.dispatch(
    'builder/world_patch',
    {
      maintenance_mode: newValue,
      maintenance_msg: maintenance_msg.value,
    });
};

</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.actions > button[disabled] {
  color: $color-text-half;
  border-color: $color-text-half;
  cursor: not-allowed;
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
