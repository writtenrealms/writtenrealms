<template>
  <div id="builder" v-if="world">
    <!-- Top nav -->
    <div class="builder-nav">
      <!-- World -->
      <router-link v-if="world" :class="{ 'active-context': (viewType === 'world') }" :to="{ name: 'builder_world_index', params: {world_id: worldId}}">
        <span>{{ world.name }}</span>
      </router-link>
      <a v-else></a>

      <div class="separator"></div>

      <!-- Zone -->
      <router-link v-if="zone" :class="{ 'active-context': (viewType === 'zone') }" :to="zoneIndexRoute">
        <span>{{ zone.name }}</span>
      </router-link>
      <a v-else></a>

      <div class="separator"></div>

      <!-- Room -->
      <router-link v-if="room" :class="{ 'active-context': (viewType === 'room') }" :to="roomIndexRoute">
        <span>{{ room.name }}</span>
      </router-link>
      <a v-else></a>
    </div>

    <!-- Side nav -->
    <div class="builder-contents-outer">
      <div class="side-nav navigation">
        <!-- World nav -->
        <template v-if="viewType === 'world'">

          <router-link
              :to="{name: 'lobby_world_details', params: {world_id: route.params.world_id}}"
          >Lobby</router-link>

          <router-link
            :to="{name: 'builder_mob_definition_list', params: {world_id: route.params.world_id}}" :class="{ 'router-link-active': isWorldMobsRoute }"
          >Mobs</router-link>

          <router-link
            :to="{name: 'builder_item_definition_list', params: {world_id: route.params.world_id}}" :class="{ 'router-link-active': isWorldItemsRoute }"
          >Items</router-link>

          <router-link
            :to="{name: 'builder_world_quest_template_list', params: {world_id: route.params.world_id}}" :class="{ 'router-link-active': isWorldQuestRoute }"
          >Quests</router-link>

          <router-link
            :to="{name: 'builder_world_config', params: {world_id: route.params.world_id}}" :class="{ 'router-link-active': isWorldConfigRoute }"
          >Config</router-link>

          <router-link
            :to="{name: 'builder_world_edit', params: {world_id: route.params.world_id}}"
          >Edit</router-link>

          <div class="mobile-hidden">
            <div class="line-divider my-2"></div>

            <router-link
              :to="{name: 'builder_zone_list', params: { world_id: route.params.world_id}}"
            >Zones</router-link>

            <router-link :to="world_admin_link" :class="{ 'router-link-active' :isWorldAdminRoute }">Admin</router-link>

            <router-link
              :to="{name: 'builder_world_builder_list', params: {world_id: route.params.world_id}}"
              :class="{ 'router-link-active': isWorldBuilderRoute }"
            >Builders</router-link>

            <router-link
              :to="{name: 'builder_world_player_list', params: {world_id: route.params.world_id}}"
            >Players</router-link>

            <router-link :to="world_factions_link">Factions</router-link>

            <router-link
              v-if="world?.builder_info?.builder_rank > 2"
              :to="{name: 'builder_world_export', params: {world_id: route.params.world_id}}"
            >Export</router-link>

          </div>
        </template>

        <!-- Zone nav -->
        <template v-else-if="viewType === 'zone'">
          <router-link
            :to="{name: 'builder_zone_room_list', params: { world_id: $route.params.world_id, zone_relative_id: route.params.zone_relative_id}}"
          >Rooms</router-link>

          <router-link
            :to="{name: 'builder_zone_path_list', params: { world_id: $route.params.world_id, zone_relative_id: route.params.zone_relative_id}}" :class="{ 'router-link-active': isZonePathRoute }"
          >Paths</router-link>

          <router-link
            :to="{name: 'builder_zone_spawn_plan_list', params: { world_id: $route.params.world_id, zone_relative_id: route.params.zone_relative_id}}" :class="{ 'router-link-active': isZoneSpawnPlanRoute }"
          >Spawns</router-link>

          <router-link
            :to="{name: 'builder_zone_config', params: { world_id: $route.params.world_id, zone_relative_id: route.params.zone_relative_id}}" :class="{ 'router-link-active': isZoneConfigRoute }"
          >Config</router-link>
        </template>

        <!-- Room nav -->
        <template v-else-if="viewType === 'room'">
          <router-link
            :to="{name: 'builder_room_spawn_plan_list', params: { world_id: $route.params.world_id, room_relative_id: room.relative_id}}"
          >
            Spawns
            <span v-if="room && room.num_spawn_plan_entries">({{ room.num_spawn_plan_entries}})</span>
          </router-link>

          <router-link
            :to="{name: 'builder_room_edit', params: { world_id: $route.params.world_id, room_relative_id: room.relative_id}}"
          >
            Edit
          </router-link>

          <router-link :to="{name: 'builder_room_trigger_list', params: { world_id: $route.params.world_id, room_relative_id: room.relative_id }}">
            Triggers
            <span v-if="room && room.num_triggers">({{ room.num_triggers }})</span>
          </router-link>

          <router-link
            :to="{name: 'builder_room_config', params: { world_id: $route.params.world_id, room_relative_id: room.relative_id}}"
          >Config</router-link>

          <router-link
            :to="{name: 'builder_room_details_list', params: { world_id: $route.params.world_id, room_relative_id: room.relative_id}}"
          >
            Details
            <span
              v-if="room && room.details && room.details.length"
            >({{ room.details.length }})</span>
          </router-link>
        </template>
      </div>

      <div class="builder-contents">
        <router-view :key="builderViewKey" v-if="builderContextReady"></router-view>
        <div v-else-if="builderContextError" class="color-text-60">
          {{ builderContextError }}
        </div>
        <div v-else>Loading...</div>
      </div>
    </div>
  </div>
  <div v-else class="loading-container">
    <div class="spinner"></div>
  </div>
</template>

<script lang='ts' setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { useStore } from 'vuex';
import { useRoute } from 'vue-router';
import axios from 'axios';
import {
  builderRoomIndexRoute,
  builderZoneIndexRoute,
  isBuilderRoomContextRoute,
  isBuilderZoneContextRoute,
} from '@/core/builderRoutes';

const store = useStore();
const route = useRoute();

// EditWorld treats its query as a request to initialize a new editor session.
// Other builder views can react to query changes without being remounted.
const queryRemountRoutes = new Set(['builder_world_edit']);

const builderViewKey = computed(() => (
  queryRemountRoutes.has(String(route.name)) ? route.fullPath : route.path
));

const world = computed(() => store.state.builder.world);
const worldId = computed(() => route.params.world_id);
const room = computed(() => store.state.builder.room);
const zone = computed(() => store.state.builder.zone);
const map = computed(() => store.state.builder.map);
let worldContextLoadId = 0;
let roomContextLoadId = 0;
let zoneContextLoadId = 0;
const roomContextError = ref('');
const zoneContextError = ref('');

const routeParam = (param) => {
  return Array.isArray(param) ? param[0] : param;
};

const routeWorldId = computed(() => routeParam(route.params.world_id));
const routeRoomRelativeId = computed(() => routeParam(route.params.room_relative_id));
const routeZoneRelativeId = computed(() => routeParam(route.params.zone_relative_id));

const cancelPendingBuilderRequest = () => {
  if (store.state.builder.cancelPreviousRequest) {
    store.state.builder.cancelPreviousRequest();
  }
};

const roomContextReady = computed(() => {
  if (!routeRoomRelativeId.value) return true;
  return (
    String(room.value?.relative_id) === String(routeRoomRelativeId.value)
    && typeof room.value?.has_assignment === 'boolean'
    && (
      !room.value?.zone?.id
      || store.state.builder.zone?.id === room.value.zone.id
    )
  );
});
const zoneContextReady = computed(() => {
  if (!routeZoneRelativeId.value) return true;
  return (
    String(zone.value?.relative_id) === String(routeZoneRelativeId.value)
    && typeof zone.value?.has_assignment === 'boolean'
  );
});
const builderContextReady = computed(() => (
  Boolean(map.value)
  && roomContextReady.value
  && zoneContextReady.value
));
const builderContextError = computed(() => roomContextError.value || zoneContextError.value);

const zoneIndexRoute = computed(() => builderZoneIndexRoute(
  route.params.world_id,
  zone.value,
));
const roomIndexRoute = computed(() => builderRoomIndexRoute(
  route.params.world_id,
  room.value,
));

const world_admin_link = computed(() => {
    return {
      name: 'builder_world_admin',
      params: { world_id: route.params.world_id },
    };
});
const world_factions_link = computed(() => {
  return {
    name: 'builder_world_faction_list',
    params: { world_id: route.params.world_id },
  };
});

const loadRouteZoneContext = async (worldId, relativeId) => {
  const loadId = ++zoneContextLoadId;
  cancelPendingBuilderRequest();
  zoneContextError.value = '';
  if (!relativeId) return null;
  if (
    String(zone.value?.relative_id) === String(relativeId)
    && typeof zone.value?.has_assignment === 'boolean'
  ) {
    if (zone.value.center && room.value?.zone?.id !== zone.value.id) {
      store.commit('builder/room_set', zone.value.center);
    }
    return zone.value;
  }

  const source = axios.CancelToken.source();
  store.commit('builder/setCancellationFunction', source.cancel);

  let loadedZone;
  try {
    loadedZone = await store.dispatch('builder/zone_relative_fetch', {
      world_id: worldId,
      zone_relative_id: relativeId,
      cancelToken: source.token,
      commit_zone: false,
      throw_on_error: true,
    });
  } catch (error: any) {
    if (
      axios.isCancel(error)
      || loadId !== zoneContextLoadId
      || String(routeWorldId.value) !== String(worldId)
      || String(routeZoneRelativeId.value) !== String(relativeId)
    ) {
      return null;
    }
    zoneContextError.value = (
      error?.response?.data?.detail
      || error?.message
      || `Zone ${relativeId} could not be loaded.`
    );
    return null;
  }

  if (!loadedZone) return null;
  if (
    loadId !== zoneContextLoadId
    || String(routeWorldId.value) !== String(worldId)
    || String(routeZoneRelativeId.value) !== String(relativeId)
  ) {
    return null;
  }

  store.commit('builder/zone_set', loadedZone);
  if (loadedZone.center && room.value?.zone?.id !== loadedZone.id) {
    store.commit('builder/room_set', loadedZone.center);
  }
  return loadedZone;
};

const loadRouteRoomContext = async (worldId, relativeId) => {
  const loadId = ++roomContextLoadId;
  cancelPendingBuilderRequest();
  roomContextError.value = '';
  if (!relativeId) return null;
  if (
    String(room.value?.relative_id) === String(relativeId)
    && typeof room.value?.has_assignment === 'boolean'
    && (
      !room.value?.zone?.id
      || store.state.builder.zone?.id === room.value.zone.id
    )
  ) {
    return room.value;
  }

  const source = axios.CancelToken.source();
  store.commit('builder/setCancellationFunction', source.cancel);

  let loadedRoom;
  try {
    loadedRoom = await store.dispatch('builder/room_fetch', {
      world_id: worldId,
      room_relative_id: relativeId,
      cancelToken: source.token,
      commit_room: false,
      throw_on_error: true,
    });
  } catch (error: any) {
    if (
      axios.isCancel(error)
      || loadId !== roomContextLoadId
      || String(routeWorldId.value) !== String(worldId)
      || String(routeRoomRelativeId.value) !== String(relativeId)
    ) {
      return null;
    }
    roomContextError.value = (
      error?.response?.data?.detail
      || error?.message
      || `Room ${relativeId} could not be loaded.`
    );
    return null;
  }
  if (!loadedRoom) return null;
  if (
    loadId !== roomContextLoadId
    || String(routeWorldId.value) !== String(worldId)
    || String(routeRoomRelativeId.value) !== String(relativeId)
  ) {
    return null;
  }
  store.commit('builder/room_set', loadedRoom);

  if (loadedRoom.zone?.id && store.state.builder.zone?.id !== loadedRoom.zone.id) {
    try {
      await store.dispatch('builder/zone_fetch', {
        world_id: worldId,
        zone_id: loadedRoom.zone.id,
        cancelToken: source.token,
        throw_on_error: true,
      });
    } catch (error: any) {
      if (
        axios.isCancel(error)
        || loadId !== roomContextLoadId
        || String(routeWorldId.value) !== String(worldId)
        || String(routeRoomRelativeId.value) !== String(relativeId)
      ) {
        return null;
      }
      roomContextError.value = (
        error?.response?.data?.detail
        || error?.message
        || `The zone for room ${relativeId} could not be loaded.`
      );
      return null;
    }
  }
  return loadedRoom;
};

const fetchWorldInfo = async (worldId) => {
  const loadId = ++worldContextLoadId;
  zoneContextLoadId += 1;
  roomContextLoadId += 1;
  cancelPendingBuilderRequest();
  zoneContextError.value = '';
  roomContextError.value = '';
  store.commit('builder/reset_state');

  const [worldResp, mapResp] = await Promise.all([
    axios.get(`/builder/worlds/${worldId}/`),
    axios.get(`/builder/worlds/${worldId}/map/`),
  ]);

  if (loadId !== worldContextLoadId || String(routeWorldId.value) !== String(worldId)) {
    return null;
  }

  const world = worldResp.data;
  store.commit('builder/world_set', world);
  store.commit('builder/map_set', mapResp.data.rooms);

  const room = world.last_viewed_room;
  if (room) {
    store.commit('builder/room_set', room);
    store.commit('builder/zone_set', room.zone);
  }

  if (routeZoneRelativeId.value) {
    await loadRouteZoneContext(worldId, routeZoneRelativeId.value);
  } else if (routeRoomRelativeId.value) {
    await loadRouteRoomContext(worldId, routeRoomRelativeId.value);
  }

  return world;
};

watch(
  routeWorldId,
  async (worldId) => {
    if (!worldId) return;
    await fetchWorldInfo(worldId);
  },
  { immediate: true },
);

watch(
  routeRoomRelativeId,
  async (relativeId) => {
    const worldId = routeWorldId.value;
    if (!relativeId) {
      roomContextLoadId += 1;
      roomContextError.value = '';
      return;
    }
    if (!worldId || String(world.value?.id) !== String(worldId)) return;
    await loadRouteRoomContext(worldId, relativeId);
  },
);

watch(
  routeZoneRelativeId,
  async (relativeId) => {
    const worldId = routeWorldId.value;
    if (!relativeId) {
      zoneContextLoadId += 1;
      zoneContextError.value = '';
      if (!routeRoomRelativeId.value) cancelPendingBuilderRequest();
      return;
    }
    if (!worldId || String(world.value?.id) !== String(worldId)) return;
    await loadRouteZoneContext(worldId, relativeId);
  },
);

onUnmounted(async () => {
  worldContextLoadId += 1;
  roomContextLoadId += 1;
  zoneContextLoadId += 1;
  cancelPendingBuilderRequest();
  store.commit('builder/reset_state');
});

const viewType = computed(() => {
  if (
    store.state.builder.room
    && isBuilderRoomContextRoute(route.name, routeRoomRelativeId.value)
  ) {
    return 'room';
  }
  if (
    store.state.builder.zone
    && isBuilderZoneContextRoute(route.name, routeZoneRelativeId.value)
  ) {
    return 'zone';
  }
  return 'world';
});

/* Active route checks */
// World
const isWorldItemsRoute = computed(() => {
  const routes = [
    'builder_item_definition_list',
    'builder_item_definition_details',
  ];
  return routes.includes(route.name as string);
});
const isWorldMobsRoute = computed(() => {
  const routes = [
    'builder_mob_definition_list',
    'builder_mob_definition_details',
  ];
  return routes.includes(route.name as string);
});
const isWorldQuestRoute = computed(() => {
  const routes = [
    'builder_world_quest_template_list',
    'builder_world_quest_template_new',
    'builder_world_quest_template_details',
  ];
  return routes.includes(route.name as string);
});
const isWorldConfigRoute = computed(() => {
  const routes = [
    'builder_world_fact_list',
    'builder_world_ability_list',
    'builder_world_ability_details',
    'builder_world_trigger_list',
    'builder_world_trigger_details',
    'builder_item_bundle_list',
    'builder_item_bundle_details',
    'builder_merchant_profile_list',
    'builder_merchant_profile_details',
    'builder_world_craft_material_list',
    'builder_world_craft_material_details',
    'builder_world_crafting_recipe_list',
    'builder_world_crafting_recipe_details',
    'builder_world_crafting_profile_list',
    'builder_world_crafting_profile_details',
    'builder_world_social_list',
    'builder_world_currency_list',
    'builder_world_instance_list',
  ];
  return routes.includes(route.name as string);
});
const isWorldAdminRoute = computed(() => { return route.name === 'builder_world_admin_instance'; });
const isWorldBuilderRoute = computed(() => {
  const routes = [
    'builder_world_builder_list',
    'builder_world_builder_assignment_list',
  ]
  return routes.includes(route.name as string);
});
// Zone
const isZonePathRoute = computed(() => {
  return ['builder_zone_path_list', 'builder_zone_path_details'].includes(route.name as string);
});
const isZoneSpawnPlanRoute = computed(() => {
  return ['builder_zone_spawn_plan_list', 'builder_zone_spawn_plan_details'].includes(route.name as string);
});
const isZoneConfigRoute = computed(() => {
  return [
    'builder_zone_config',
    'builder_zone_procession_list',
    'builder_zone_procession_details',
  ].includes(route.name as string);
});
</script>

<style lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
@import "@/styles/layout.scss";

#builder {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  max-width: 100%;
  overflow-x: clip;

  .builder-nav {
    box-sizing: border-box;
    display: flex;
    flex-wrap: nowrap;
    justify-content: space-evenly;
    max-width: 100%;
    min-width: 0;
    overflow-x: clip;

    flex-shrink: 0;

    // http://stackoverflow.com/questions/6250394/how-to-increase-space-between-dotted-border-dots
    background-image: linear-gradient(
      to right,
      #444 33%,
      rgba(255, 255, 255, 0) 0%
    );
    background-position: bottom;
    background-size: 3px 1px;
    background-repeat: repeat-x;

    @media ($desktop-site) {
      min-height: 50px;
    }

    > a {
      color: $color-text-hex-30;
      font-weight: 300;
      display: flex;
      padding: 10px;
      flex-grow: 1;
      flex-shrink: 0;
      flex-basis: 0;
      justify-content: center;
      align-items: center;
      text-align: center;

      &.router-link-exact-active,
      &.active-context {
        color: $color-text;
      }

      @media ($desktop-site) {
        padding: 15px;
      }
    }

    .separator {
      display: flex;
      flex-direction: column;
      justify-content: center;

      // Create the thin right-headed arrows

      width: 15px;
      //height: 50px;
      position: relative;

      $arrow-color: $color-text-hex-30;
      $arrow-height: 15px; // *2
      $arrow-width: 10px;

      // Create two triangles
      &:after,
      &:before {
        content: "";
        position: absolute;
        width: 0;
        height: 0;
        border-style: solid;
        border-color: transparent;
        border-left-width: $arrow-width;
        border-top-width: $arrow-height;
        border-bottom-width: $arrow-height;
      }

      // Offset one by 1 pixel to the left with the same
      // background as the page to make it look thin
      &:before {
        border-left-color: $arrow-color;
        left: 1px;
      }
      &:after {
        border-left-color: $color-background;
      }
    }
  }

  .builder-contents-outer {
    box-sizing: border-box;
    display: flex;
    flex-grow: 1;
    max-width: 100%;
    min-width: 0;
    overflow-x: clip;

    /* Responsive nav set */
    @media ($desktop-site) {
      flex-direction: row;

      .side-nav {
        box-sizing: border-box;
        display: flex;
        flex-shrink: 0;
        flex-direction: column;
        padding: 15px 10px 15px 25px;
        width: 150px;
      }
    }

    .builder-contents {
      box-sizing: border-box;
      flex-grow: 1;
      display: flex;
      -webkit-overflow-scrolling: touch;
      min-width: 0;
    }

    @media ($mobile-site) {
      flex-direction: column;

      .builder-contents {
        order: 1;
      }
      .side-nav {
        order: 2;
      }
    }
    /* End Responsive nav set */

    .builder-contents {
      box-sizing: border-box;
      padding: 15px;
      flex-grow: 1;
      min-width: 0;

      @media ($mobile-site) {
        overflow-y: auto;
      }

      .entity-title {
        font-size: 24px;
        margin-bottom: 10px;
      }

      > * {
        box-sizing: border-box;
        min-width: 0;
        width: 100%;
      }
    }
  }

  .line-divider {
    width: 60%;
  }
}
</style>
