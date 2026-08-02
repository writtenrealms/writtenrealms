<template>
  <div id="room-edit">
    <div v-if="isLoading" class="color-text-60">Loading room...</div>

    <template v-else-if="room">
      <div v-if="!canEdit" class="color-text-50 mb-4">
        This room is not assigned to you, so you can view its YAML but cannot save changes.
      </div>

      <ManifestYamlEditor
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        :save-disabled="!canEdit"
        copy-success-message="Room YAML copied."
        @save="submitManifest"
      >
        <template #header>
          <h2 class="room-title">{{ room.name }}</h2>
          <div class="room-meta color-text-60">
            {{ room.manifest_ref || `room@${room.relative_id}` }} - ({{ room.x }}, {{ room.y }}, {{ room.z }})
          </div>
        </template>
      </ManifestYamlEditor>
    </template>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import ManifestYamlEditor from "@/components/builder/world/ManifestYamlEditor.vue";
import { roomRelativeIdFromRef } from "@/core/builderRoutes";

const route = useRoute();
const router = useRouter();
const store = useStore();

const isLoading = ref(false);
const isSubmitting = ref(false);
const manifestText = ref("");
const loadedYaml = ref("");

const room = computed(() => {
  const currentRoom = store.state.builder.room;
  if (String(currentRoom?.relative_id) !== String(route.params.room_relative_id)) return null;
  return currentRoom;
});
const roomDetailEndpoint = (roomId: string | number | string[]) => (
  `/builder/worlds/${route.params.world_id}/rooms/${roomId}/`
);
const roomManifestEndpoint = (roomId: string | number | string[]) => (
  `/builder/worlds/${route.params.world_id}/rooms/${roomId}/manifest/`
);
const manifestApplyEndpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/manifests/apply/`
));
const canEdit = computed(() => room.value?.has_assignment === true);

const extractError = (error: any, fallbackMessage: string): string => {
  const data = error?.response?.data;
  if (!data) return error?.message || fallbackMessage;
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || fallbackMessage;
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0] || fallbackMessage;
    if (typeof value === "string") return value;
  }
  return fallbackMessage;
};

const setLoadedManifest = (payload: any) => {
  if (typeof payload?.yaml !== "string") {
    throw new Error("Room manifest response did not include YAML.");
  }

  loadedYaml.value = payload.yaml;
  manifestText.value = payload.yaml;
};

const setRoomContext = async (payload: any) => {
  const previousRoom = store.state.builder.room;
  if (previousRoom?.id === payload.id) {
    store.commit("builder/map_deindex", [previousRoom]);
  }
  store.commit("builder/map_add", [payload]);
  store.commit("builder/room_set", payload);

  const zoneId = payload.zone?.id;
  if (zoneId && store.state.builder.zone?.id !== zoneId) {
    await store.dispatch("builder/zone_fetch", {
      world_id: route.params.world_id,
      zone_id: zoneId,
    });
  }
};

const requestRoomContext = async (roomId: string | number | string[]) => {
  const resp = await axios.get(roomDetailEndpoint(roomId));
  await setRoomContext(resp.data);
};

const requestRoomManifest = async (roomId: string | number | string[]) => {
  const resp = await axios.get(roomManifestEndpoint(roomId));
  setLoadedManifest(resp.data);
};

const fetchRoom = async () => {
  isLoading.value = true;
  try {
    const relativeId = route.params.room_relative_id;
    let currentRoom = store.state.builder.room;
    if (
      String(currentRoom?.relative_id) !== String(relativeId)
      || typeof currentRoom?.has_assignment !== "boolean"
    ) {
      currentRoom = await store.dispatch("builder/room_fetch", {
        world_id: route.params.world_id,
        room_relative_id: relativeId,
      });
    }
    if (!currentRoom?.id) throw new Error("Room could not be loaded.");
    await requestRoomManifest(currentRoom.id);
  } catch (error: any) {
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error, "Could not load room YAML."));
  } finally {
    isLoading.value = false;
  }
};

const submitManifest = async () => {
  isSubmitting.value = true;
  let manifestApplied = false;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });

    if (resp.data?.kind !== "room" || !resp.data?.room?.id) {
      throw new Error("Manifest apply did not return a room payload.");
    }
    manifestApplied = true;

    const appliedRoom = resp.data.room;
    const appliedRoomId = appliedRoom.id;
    const appliedRoomRelativeId = roomRelativeIdFromRef(appliedRoom.ref);
    if (!appliedRoomRelativeId) {
      throw new Error("Manifest apply did not return a portable room reference.");
    }
    if (String(appliedRoomRelativeId) !== String(route.params.room_relative_id)) {
      store.commit("ui/notification_set", `Room ${resp.data.operation}.`);
      await router.replace({
        name: "builder_room_edit",
        params: {
          world_id: route.params.world_id,
          room_relative_id: appliedRoomRelativeId,
        },
      });
      return;
    }

    await Promise.all([
      requestRoomContext(appliedRoomId),
      requestRoomManifest(appliedRoomId),
    ]);
    store.commit("ui/notification_set", `Room ${resp.data.operation}.`);
  } catch (error: any) {
    if (manifestApplied) {
      const detail = extractError(error, "The saved room could not be reloaded.");
      store.commit(
        "ui/notification_set_error",
        `Room manifest was applied, but its canonical YAML could not be reloaded. ${detail}`,
      );
    } else {
      store.commit(
        "ui/notification_set_error",
        extractError(error, "Could not apply room manifest."),
      );
    }
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchRoom);

watch(
  () => [route.params.world_id, route.params.room_relative_id],
  async (nextValue, previousValue) => {
    if (
      String(nextValue[0]) === String(previousValue[0])
      && String(nextValue[1]) === String(previousValue[1])
    ) return;
    await fetchRoom();
  },
);
</script>

<style lang="scss" scoped>
#room-edit {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}

.room-title {
  margin-bottom: 0.35rem;
}

.room-meta {
  overflow-wrap: anywhere;
}
</style>
