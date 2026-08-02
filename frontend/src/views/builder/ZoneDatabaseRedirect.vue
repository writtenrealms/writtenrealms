<template>
  <div class="color-text-60">
    {{ errorMessage || "Resolving zone..." }}
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import { zoneRelativeId } from "@/core/builderRoutes";

const route = useRoute();
const router = useRouter();
const store = useStore();
const errorMessage = ref("");
const requestSource = axios.CancelToken.source();
let isActive = true;

const resolveDatabaseZone = async () => {
  const worldId = String(route.params.world_id || "");
  const databaseId = String(route.params.zone_database_id || "");
  const routeIsCurrent = () => (
    isActive
    && route.name === "builder_zone_database_lookup"
    && String(route.params.world_id || "") === worldId
    && String(route.params.zone_database_id || "") === databaseId
  );

  try {
    const response = await axios.get(
      `/builder/worlds/${worldId}/zones/${databaseId}/`,
      { cancelToken: requestSource.token },
    );
    if (!routeIsCurrent()) return;

    const zone = response.data;
    const relativeId = zoneRelativeId(zone);
    if (!relativeId) {
      throw new Error("Zone response did not include a relative ID.");
    }

    store.commit("builder/zone_set", zone);
    if (zone.center && store.state.builder.room?.zone?.id !== zone.id) {
      store.commit("builder/room_set", zone.center);
    }

    await router.replace({
      name: "builder_zone_index",
      params: {
        world_id: worldId,
        zone_relative_id: relativeId,
      },
    });
  } catch (error: any) {
    if (axios.isCancel(error) || !routeIsCurrent()) return;
    const message = error?.response?.data?.detail || error?.message || "Could not resolve zone.";
    errorMessage.value = message;
    store.commit("ui/notification_set_error", message);
  }
};

onMounted(resolveDatabaseZone);
onUnmounted(() => {
  isActive = false;
  requestSource.cancel("Zone database lookup was superseded.");
});
</script>
