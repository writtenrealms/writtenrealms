<template>
  <div id="zone-details" v-if="zone">
    <h2 class="zone-name entity-title">{{ zone.name }}</h2>
    <div class="zone-meta color-text-60 mb-4">
      <span>Database ID {{ zone.id }}</span>
      <span>Relative ID {{ zone.relative_id }}</span>
      <span>Manifest Ref <code>{{ zone.manifest_ref }}</code></span>
      <button class="btn-thin" :disabled="!zone.manifest_ref" @click="copyManifestRef">
        COPY REF
      </button>
    </div>

    <div v-if="store.state.builder.world.builder_info.builder_rank < 3 && zone.has_assignment != undefined" class="color-text-50 mb-4">
      <span v-if="zone.has_assignment">This zone is assigned to you, you can edit it.</span>
      <span v-else>This zone is not assigned to you, you can view it but not edit it.</span>
    </div>

    <template v-if="isReady">
      <div class="zone-map-and-data" v-if="isReady">
        <div class="map-and-edit">
          <Map
            :center_key="center_key"
            :radius="5"
            :unit="8"
            :map="map"
            :rooms_filter="zone_rooms"
            @clickRoom="onClickRoom"
          />
          <div>
            <button class="btn-thin edit-mob" @click="editInfo">EDIT</button>
          </div>
        </div>

        <div class="zone-spawn-plans">
          <h3>SPAWN PLANS</h3>
          <div v-if="zone_spawn_plans.length">
            <div class="spawn-plan-row" v-for="spawnPlan in zone_spawn_plans" :key="spawnPlan.id">
              <div>{{ spawnPlan.name || spawnPlan.slug }}</div>
              <div class="color-text-50">
                {{ spawnPlan.num_entries }} entries
                <span v-if="spawnPlan.respawn_mode">/ {{ spawnPlan.respawn_mode }}</span>
              </div>
            </div>
          </div>
          <div v-else class="color-text-50">No spawn plans.</div>
        </div>
      </div>

      <div v-else-if="!zone_rooms.length">Zone has no rooms.</div>
      <div v-else class="emptymap">Loading...</div>

      <div class="zone-data" v-if="zone.zone_data && Object.keys(zone.zone_data).length">
        <h3>ZONE STATE</h3>
        <div v-for="attr of Object.keys(zone.zone_data).sort()" :key="attr">
          <dl>
            <dt>{{attr}}</dt>
            <dd>
              <span v-if="zone.zone_data[attr]">{{ zone.zone_data[attr] }}</span>
              <span v-else>null</span>
            </dd>
          </dl>
        </div>
      </div>
    </template>
    <div v-else-if="loaded && !zone_rooms.length">
      <div
        class="color-text-70"
      >Zone has no rooms. Go to an existing room and assign it to this zone to see a map.</div>
      <div>
        <button class="btn-thin edit-mob" @click="editInfo">EDIT</button>
        <button class="btn-thin" @click="deleteZone">DELETE</button>
      </div>
    </div>

    <div class="hlist respawn-frequency mt-8">
      <div class="hlist-header">
        <h3>RESPAWN FREQUENCY</h3>
      </div>
      <div class="hlist-item">
        <label>
          <input type="checkbox" :checked="respawns" @input="onChangeRespawns" />
          Respawns
        </label>

        <div class="respawn-wait mt-2" v-if="respawns">
          <div>
            <span
              v-if="zone.respawn_wait"
            >Wait {{ zone.respawn_wait }} seconds before respawning.</span>
            <span v-else>Respawns immediately.</span>
          </div>
          <button class="btn-thin" @click="editRespawns">EDIT</button>
        </div>
      </div>
    </div>

  </div>
</template>

<script lang='ts' setup>
import { ref, computed, onMounted } from "vue";
import { useStore } from "vuex";
import { useRouter, useRoute } from "vue-router";
import { selectZone } from "@/composables/selectZone";

const store = useStore();
const route = useRoute();
const router = useRouter();

import { Room } from "@/core/interfaces";
import Map from "@/components/ui/Map.vue";
import axios from "axios";
import { FormElement } from "@/core/forms.ts";

const zone_rooms = ref<Room[]>([]);
const zone_spawn_plans = ref<any[]>([]);
const loaded = ref(false);
const respawns = computed(() => store.state.builder.zone.respawn_wait >= 0);
const zone = computed(() => store.state.builder.zone);
const isReady = computed(() => {
  return Boolean(
    store.state.builder.map &&
      zone.value &&
      store.state.builder.room &&
      zone_rooms.value.length
  );
});
const center_key = computed(() => store.state.builder.room.key);
const map = computed(() => store.state.builder.map);


const onClickRoom = async (room) => {
  store.commit('builder/room_set', room);

  if (room.zone.id != route.params.zone_id) {
    await store.dispatch('builder/zone_fetch', {
      world_id: route.params.world_id,
      zone_id: room.zone.id,
    });

    // Update route
    router.push({
      name: 'builder_zone_index',
      params: {
        zone_id: room.zone.id,
        world_id: route.params.world_id
      }
    });
  }
};


onMounted(async () => {
  const world_id = route.params.world_id;
  const zone_id = route.params.zone_id;

  const zone_spawn_plans_promise = axios.get(`builder/worlds/${world_id}/zones/${zone_id}/spawn-plans/`);

  // build promise to fetch zone's rooms

  const zone_rooms_promise = store.dispatch('builder/zone_rooms_fetch', {
    world_id: world_id,
    zone_id: zone_id,
  });

  const zone_details_promise = store.dispatch('builder/zone_fetch', {
        world_id: route.params.world_id,
        zone_id: route.params.zone_id
      });

  const [
    zone_spawn_plans_resp,
    zone_rooms_resp,
    _,
  ] = await Promise.all([
    zone_spawn_plans_promise,
    zone_rooms_promise,
    zone_details_promise,
  ]);

  zone_spawn_plans.value = zone_spawn_plans_resp.data.spawn_plans || [];
  zone_rooms.value = zone_rooms_resp;
  loaded.value = true;
});

selectZone();

const editInfo = () => {
  const entity = store.state.builder.zone;

  const schema: FormElement[] = [
    {
      attr: "name",
      label: "Name",
    },
  ];

  if (store.state.builder.world.is_multiplayer) {
    schema.push({
      attr: "pvp_zone",
      label: "Allows PvP",
      default: false,
      widget: "checkbox",
    });
  }

  const modal = {
    title: `Zone ${entity.id}`,
    data: entity,
    schema: schema,
    action: 'builder/zone_save',
  };
  store.commit('ui/modal/open_form', modal);
};

const deleteZone = async () => {
  const zone = store.state.builder.zone;

  // Crude confirm dialog
  const c = confirm(`Are you sure you want to delete Zone ${zone.id}: ${zone.name}?`);
  if (!c) return;

  await store.dispatch('builder/zone_delete');
  store.commit(
    'ui/notification_set',
    `Deleted Zone ${zone.id}`
  );
};

const copyManifestRef = async () => {
  try {
    await navigator.clipboard.writeText(zone.value.manifest_ref || "");
    store.commit("ui/notification_set", "Zone manifest ref copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy zone manifest ref.");
  }
};

const onChangeRespawns = (event: Event) => {
  const checked = (event.target as HTMLInputElement).checked;
  let respawn_wait = -1;
  if (checked) respawn_wait = 300;
  store.dispatch("builder/zone_save", {
    respawn_wait: respawn_wait,
  });
};

const editRespawns = () => {
  const entity = store.state.builder.zone;
  const modal = {
    data: entity,
    schema: [
      {
        attr: "respawn_wait",
        label: "Respawn Wait",
        help: "How long to wait before re-running spawn plans, in seconds.",
      },
    ],
    action: "builder/zone_save",
  };
  store.commit('ui/modal/open_form', modal);
};

</script>

<style lang='scss' scoped>
@import "@/styles/layout.scss";
@import "@/styles/colors.scss";

#zone-details {
  width: 100%;

  .zone-meta {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;

    code {
      color: $color-text;
    }
  }

  .zone-map-and-data {
    display: flex;
    @media ($mobile-site) {
      flex-direction: column;
      .zone-spawn-plans {
        margin-top: 15px;
      }
    }
    @media ($desktop-site) {
      .zone-spawn-plans {
        margin-left: 25px;
      }
    }

    .zone-spawn-plans {
      display: flex;
      flex-direction: column;
      > div {
        &:not(:last-child) {
          margin-bottom: 10px;
        }
        h3 {
          margin-bottom: 10px;
        }
      }
    }
  }

  .emptymap {
    border: 1px solid #444;
    width: 272px;
    height: 272px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
  }

  .zone-data {
    width: 320px;

    margin-top: 30px;
    h3 {
      margin-bottom: 15px;
    }

    dl {
      display: flex;
      flex-wrap: wrap;
      dt {
        color: $color-text-hex-70;
        width: 45%;
        text-align: right;
        margin-right: 5%;
      }
      dd {
        margin-left: 5%;
        width: 45%;
      }
    }
  }

  .respawn-frequency {
    width: 300px;
  }
}
</style>
