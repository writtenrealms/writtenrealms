<template>
  <div id="zone-details" v-if="zone">
    <h2 class="zone-name">{{ zone.name }}</h2>
    <div class="zone-meta color-text-60">
      {{ zone.id }} - {{ zone.manifest_ref }}
    </div>
    <div class="zone-actions mb-4">
      <button class="btn-small" :disabled="!zoneYaml" @click="copyZoneYaml">
        COPY YAML
      </button>
      <button class="btn-thin" :disabled="!zoneDeleteYaml" @click="copyZoneDeleteYaml">
        COPY DELETE YAML
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
const zoneYaml = computed(() => zone.value?.yaml || buildZoneYaml(zone.value));
const zoneDeleteYaml = computed(() => zone.value?.delete_yaml || buildZoneDeleteYaml(zone.value));


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

const copyZoneYaml = async () => {
  try {
    await copyText(zoneYaml.value);
    store.commit("ui/notification_set", "Zone YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy zone YAML to clipboard.");
  }
};

const copyZoneDeleteYaml = async () => {
  try {
    await copyText(zoneDeleteYaml.value);
    store.commit("ui/notification_set", "Zone delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy zone delete YAML to clipboard.");
  }
};

const copyText = async (value: string) => {
  if (!value.trim()) throw new Error("Nothing to copy.");
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Fall back for browsers/extensions that reject clipboard writes.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-1000px";
  textarea.style.left = "-1000px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const didCopy = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!didCopy) throw new Error("Copy failed.");
};

const roomRef = (room: any) => {
  if (!room || room.x === undefined || room.y === undefined || room.z === undefined) return "";
  return `room@${room.x},${room.y},${room.z}`;
};

const yamlScalar = (value: any): string => {
  if (value === null || value === undefined) return "''";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "0";
  const text = String(value);
  if (!text) return "''";
  if (/^[A-Za-z0-9_@./-]+(?: [A-Za-z0-9_@./-]+)*$/.test(text)) return text;
  return JSON.stringify(text);
};

const yamlBlock = (value: any, indent = 0): string[] => {
  const prefix = " ".repeat(indent);
  if (!value || typeof value !== "object") return [`${prefix}${yamlScalar(value)}`];
  if (Array.isArray(value)) {
    if (!value.length) return [`${prefix}[]`];
    return value.flatMap((item) => {
      if (item && typeof item === "object") {
        const [first, ...rest] = yamlBlock(item, indent + 2);
        return [`${prefix}- ${first.trimStart()}`, ...rest];
      }
      return [`${prefix}- ${yamlScalar(item)}`];
    });
  }
  const entries = Object.entries(value);
  if (!entries.length) return [`${prefix}{}`];
  return entries.flatMap(([key, item]) => {
    if (item && typeof item === "object") {
      return [`${prefix}${key}:`, ...yamlBlock(item, indent + 2)];
    }
    return [`${prefix}${key}: ${yamlScalar(item)}`];
  });
};

const buildZoneYaml = (zoneValue: any): string => {
  if (!zoneValue?.manifest_ref || !zoneValue?.name) return "";
  const spec: Record<string, any> = {};
  if (zoneValue.zone_data && Object.keys(zoneValue.zone_data).length) {
    spec.state = zoneValue.zone_data;
  }
  if (zoneValue.respawn_wait !== undefined && zoneValue.respawn_wait !== null) {
    spec.respawn_wait = Number(zoneValue.respawn_wait);
  }
  if (zoneValue.pvp_zone !== undefined && zoneValue.pvp_zone !== null) {
    spec.pvp_zone = Boolean(zoneValue.pvp_zone);
  }
  const centerRef = roomRef(zoneValue.center);
  if (centerRef) spec.center = centerRef;

  return [
    "kind: zone",
    "metadata:",
    `  ref: ${yamlScalar(zoneValue.manifest_ref)}`,
    `  name: ${yamlScalar(zoneValue.name)}`,
    "spec:",
    ...yamlBlock(spec, 2),
    "",
  ].join("\n");
};

const buildZoneDeleteYaml = (zoneValue: any): string => {
  if (!zoneValue?.manifest_ref || !zoneValue?.name) return "";
  return [
    "kind: zone",
    "operation: delete",
    "metadata:",
    `  ref: ${yamlScalar(zoneValue.manifest_ref)}`,
    `  name: ${yamlScalar(zoneValue.name)}`,
    "",
  ].join("\n");
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

  .zone-name {
    margin-bottom: 0.35rem;
  }

  .zone-meta {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .zone-actions {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.75rem;
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
