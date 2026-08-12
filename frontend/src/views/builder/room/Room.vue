<template>
  <div id="room-details" v-if="room">
    <h2 class="entity-title">{{ room.name }}</h2>

    <div v-if="store.state.builder.world.builder_info.builder_rank < 3 && room.has_assignment != undefined" class="color-text-50 mb-4">
      <span v-if="room.has_assignment">This room is assigned to you, you can edit it.</span>
      <span v-else>This room is not assigned to you, you can view it but not edit it.</span>
    </div>

    <div v-if="isMapReady" class="info-and-map">
      <div class="id-map-coords">
        <div class="id-and-coords">
          <div class="room-reference">{{ manifestRef }}</div>
          <div class="room-coordinates">({{ room.x }}, {{ room.y }}, {{ room.z}})</div>
        </div>
        <Map
          v-if="isMapReady"
          :map="map"
          :center_key="center_key"
          :unit="8"
          :radius="3"
          :display_planes="true"
          @clickRoom="onMapClickRoom"
        />
        <div class="basic-actions mt-1">
          <button class="btn-small mr-4" @click="editInfo">EDIT</button>
          <button class="btn-small" @click="deleteRoom">DELETE</button>
        </div>
      </div>

      <div class="room-description">
        <div class="description-title">Description</div>
        <RoomDescription v-if="room.description" :room="room" />
        <div class="description-content no-description" v-else>Room has no description.</div>
        <button class="btn-thin" @click="onEditDescription">EDIT DESCRIPTION</button>
      </div>
    </div>

    <div class='flex actions-and-doors'>
      <RoomDirActions />

      <div class='doors' v-if="roomDoors.length">
        <h3>DOORS</h3>
        <div v-for="door in roomDoors" :key="door.direction">
          {{ door.direction }}: {{ door.name || "unnamed" }}
          [{{ door.default_state }}]
          <template v-if="door.key">
            - opened by <router-link :to="key_link(door.key)">{{ door.key.name }}</router-link>
          </template>
        </div>
      </div>
    </div>

    <div class="mt-8" v-if="room.note">
      <h3>ROOM NOTE</h3>
      <div>{{ room.note }}</div>
    </div>

    <details class="technical-details mt-8">
      <summary>Technical details</summary>
      <dl>
        <div>
          <dt>Room reference</dt>
          <dd class="technical-room-reference">
            {{ manifestRef }}
            <button class="btn-thin copy-room-reference" @click="copyManifestRef">COPY</button>
          </dd>
        </div>
        <div>
          <dt>Relative ID</dt>
          <dd>{{ room.relative_id }}</dd>
        </div>
        <div v-if="store.state.auth.user.is_staff">
          <dt>Database ID</dt>
          <dd>{{ room.id }}</dd>
        </div>
      </dl>
    </details>
  </div>
</template>

<script lang='ts' setup>
import { computed, onMounted, onUnmounted } from "vue";
import { useStore } from "vuex";
import { useRoute, useRouter } from "vue-router";
import Map from "@/components/ui/Map.vue";
import { DIRECTIONS } from "@/constants";
import RoomDirActions from "@/components/builder/room/RoomDirActions.vue";
import { BUILDER_FORMS } from "@/core/forms";
import { getMovementDirectionFromArrowKey } from "@/core/keyboard";
import { builderRoomIndexRoute } from "@/core/builderRoutes";
import RoomDescription from "@/components/builder/room/RoomDescription.vue";

const store = useStore();
const route = useRoute();
const router = useRouter();

const map = computed(() => store.state.builder.map);
const center_key = computed(() => store.state.builder.room.key);
const room = computed(() => store.state.builder.room);
const manifestRef = computed(() => room.value?.manifest_ref || `room@${room.value?.relative_id}`);
const roomDoors = computed<any[]>(() => {
  if (!room.value.doors) return [];
  const doors: {}[] = [];
  for (const direction in DIRECTIONS) {
    if (room.value.doors[direction]) {
      doors.push(room.value.doors[direction]);
    }
  }
  return doors;
});

const isMapReady = computed(() => Boolean(store.state.builder.room && store.state.builder.map));

const key_link = (key: any) => {
  return {
    name: 'builder_item_definition_details',
    params: {
      world_id: route.params.world_id,
      item_definition_id: key.id
    }
  };
};

const isEditableTarget = (target: EventTarget | null) => {
  return target instanceof HTMLElement && (
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
    target.isContentEditable
  );
};

const goToRoom = (nextRoom) => {
  if (!nextRoom?.id && !nextRoom?.relative_id && !nextRoom?.manifest_ref) return;
  router.push(builderRoomIndexRoute(route.params.world_id, nextRoom));
};

const moveToDirection = (direction: string) => {
  const exitRoomRef = room.value?.[direction];
  if (!exitRoomRef) return;

  const nextRoom = map.value?.[exitRoomRef.key] || exitRoomRef;
  goToRoom(nextRoom);
};

const onKeyDown = (e: KeyboardEvent) => {
  if (store.state.ui.modal.isOpen || store.state.ui.editingField) return;
  if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
  if (isEditableTarget(e.target)) return;

  const direction = getMovementDirectionFromArrowKey(e);
  if (direction) {
    e.preventDefault();
    e.stopPropagation();
    moveToDirection(direction);
    return;
  }

  if (e.key.toLowerCase() === "e") {
    e.preventDefault();
    e.stopPropagation();
    editInfo();
  }
};

onMounted(() => {
  window.addEventListener("keydown", onKeyDown);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeyDown);
});

const editInfo = () => {
  const modal = {
    data: room.value,
    schema: BUILDER_FORMS.ROOM_INFO,
    action: "builder/room_save"
  };
  store.commit("ui/modal/open_form", modal);
};

const deleteRoom = async () => {
  const room_ref = manifestRef.value;
  const c = confirm(`Are you sure you want to delete ${room_ref}?`);
  if (!c) return;
  await store.dispatch("builder/room_delete");
  store.commit("ui/notification_set", `Deleted ${room_ref}.`);
};

const copyManifestRef = async () => {
  try {
    await navigator.clipboard.writeText(manifestRef.value);
    store.commit("ui/notification_set", `${manifestRef.value} copied.`);
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy room reference to clipboard.");
  }
};

const onEditDescription = () => {
  const modal = {
    class: "description-modal",
    data: store.state.builder.room,
    schema: [BUILDER_FORMS.DESCRIPTION],
    action: "builder/room_save"
  };
  store.commit("ui/modal/open_form", modal);
};

const onMapClickRoom = (room) => {
  goToRoom(room);
};
</script>

<style lang='scss' scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

#room-details {
  width: 100%;
  max-width: 800px;

  .info-and-map {
    display: flex;
    width: 100%;

    .id-map-coords {
      margin-right: 20px;

      .id-and-coords {
        display: flex;
        justify-content: space-between;
        margin-bottom: 0.5em;

        .room-reference {
          color: $color-text-70;
        }
      }
    }

    .room-description {
      flex-grow: 1;
      .description-title {
        color: $color-text-70;
        margin-bottom: 0.5em;
      }
      .description-content {
        border: 1px dotted $color-form-border;
        padding: 5px 10px;
        height: 178px;
        overflow-y: auto;
      }
      button {
        margin-top: 8px;
      }
    }
  }

  .technical-details {
    color: $color-text-70;

    summary {
      cursor: pointer;
    }

    dl {
      margin: 0.75rem 0 0;
    }

    dl > div {
      display: flex;
      gap: 0.75rem;
      margin-bottom: 0.25rem;
    }

    dt {
      min-width: 105px;
    }

    dd {
      color: $color-text;
      margin: 0;
    }

    .technical-room-reference {
      align-items: center;
      display: flex;
      gap: 0.5rem;
    }

    .copy-room-reference {
      font-size: 0.65rem;
      padding: 1px 4px;
    }
  }

  .actions-and-doors {
    @media ($mobile-site) {
      flex-direction: column;
    }
    @media ($desktop-site) {
      .doors {
        margin-left: 60px;
        margin-top: 40px;
      }
    }
  }
}
</style>
