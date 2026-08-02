<template>
  <div class="color-text-60">
    {{ errorMessage || "Resolving room..." }}
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import { roomRelativeId } from "@/core/builderRoutes";

const route = useRoute();
const router = useRouter();
const store = useStore();
const errorMessage = ref("");
const requestSource = axios.CancelToken.source();
let isActive = true;

const resolveDatabaseRoom = async () => {
  const worldId = String(route.params.world_id || "");
  const databaseId = String(route.params.room_database_id || "");
  const routeIsCurrent = () => (
    isActive
    && route.name === "builder_room_database_lookup"
    && String(route.params.world_id || "") === worldId
    && String(route.params.room_database_id || "") === databaseId
  );
  try {
    const response = await axios.get(
      `/builder/worlds/${worldId}/rooms/${databaseId}/`,
      { cancelToken: requestSource.token },
    );
    if (!routeIsCurrent()) return;
    const room = response.data;
    const relativeId = roomRelativeId(room);
    if (!relativeId) {
      throw new Error("Room response did not include a relative ID.");
    }

    const previousRoom = store.state.builder.room;
    if (previousRoom?.id === room.id) {
      store.commit("builder/map_deindex", [previousRoom]);
    }
    store.commit("builder/map_add", [room]);
    store.commit("builder/room_set", room);
    if (room.zone?.id && store.state.builder.zone?.id !== room.zone.id) {
      await store.dispatch("builder/zone_fetch", {
        world_id: worldId,
        zone_id: room.zone.id,
        cancelToken: requestSource.token,
      });
    }
    if (!routeIsCurrent()) return;

    await router.replace({
      name: "builder_room_index",
      params: {
        world_id: worldId,
        room_relative_id: relativeId,
      },
    });
  } catch (error: any) {
    if (axios.isCancel(error) || !routeIsCurrent()) return;
    const message = error?.response?.data?.detail || error?.message || "Could not resolve room.";
    errorMessage.value = message;
    store.commit("ui/notification_set_error", message);
  }
};

onMounted(resolveDatabaseRoom);
onUnmounted(() => {
  isActive = false;
  requestSource.cancel("Room database lookup was superseded.");
});
</script>
