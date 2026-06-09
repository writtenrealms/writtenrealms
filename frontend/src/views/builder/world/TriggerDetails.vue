<template>
  <div id="trigger-details">
    <div v-if="isLoading" class="color-text-60">Loading trigger...</div>

    <template v-else-if="trigger">
      <ManifestYamlEditor
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        copy-success-message="Trigger YAML copied."
        @save="submitManifest"
      >
        <template #header>
          <h2>{{ trigger.name || trigger.key }}</h2>
          <div class="color-text-60">
            ID: {{ trigger.id }} | Key: {{ trigger.key }} | {{ trigger.scope }} / {{ trigger.kind }}
          </div>
        </template>
        <template #actions>
          <button class="btn-thin" :disabled="!trigger.delete_yaml" @click="copyDeleteYaml">
            COPY DELETE YAML
          </button>
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

const route = useRoute();
const router = useRouter();
const store = useStore();

const trigger = ref<any | null>(null);
const isLoading = ref(false);
const isSubmitting = ref(false);
const manifestText = ref("");
const loadedYaml = ref("");
const endpoint = computed(() => (
  route.params.room_id
    ? `/builder/worlds/${route.params.world_id}/rooms/${route.params.room_id}/triggers/${route.params.trigger_id}/`
    : `/builder/worlds/${route.params.world_id}/triggers/${route.params.trigger_id}/`
));
const manifestApplyEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);

const extractError = (error: any, fallbackMessage = "Could not load trigger."): string => {
  const data = error?.response?.data;
  if (!data) return fallbackMessage;
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || fallbackMessage;
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return fallbackMessage;
};

const setLoadedState = (payload: any) => {
  trigger.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
};

const fetchTrigger = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    setLoadedState(resp.data);
  } catch (error: any) {
    trigger.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyDeleteYaml = async () => {
  try {
    await navigator.clipboard.writeText(trigger.value?.delete_yaml || "");
    store.commit("ui/notification_set", "Trigger delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy delete YAML to clipboard.");
  }
};

const parseEntityIdFromKey = (key: any): string | null => {
  const match = String(key || "").match(/(?:^|\.)(\d+)$/);
  return match ? match[1] : null;
};

const routeForTrigger = (payload: any) => {
  const id = payload?.id;
  if (!id) return null;
  const target = payload?.target || {};
  const targetRoomId = target.type === "room" ? parseEntityIdFromKey(target.key) : null;
  if (route.params.room_id && targetRoomId) {
    return {
      name: "builder_room_trigger_details",
      params: {
        world_id: route.params.world_id,
        room_id: targetRoomId,
        trigger_id: id,
      },
    };
  }
  return {
    name: "builder_world_trigger_details",
    params: {
      world_id: route.params.world_id,
      trigger_id: id,
    },
  };
};

const syncRouteToTrigger = async (payload: any) => {
  const targetRoute = routeForTrigger(payload);
  if (!targetRoute) return;
  const params = targetRoute.params as Record<string, any>;
  if (
    String(route.params.trigger_id) === String(params.trigger_id)
    && String(route.params.room_id || "") === String(params.room_id || "")
  ) {
    return;
  }
  await router.replace(targetRoute);
};

const redirectAfterDelete = async () => {
  await router.push({
    name: route.params.room_id ? "builder_room_trigger_list" : "builder_world_trigger_list",
    params: {
      world_id: route.params.world_id,
      ...(route.params.room_id ? { room_id: route.params.room_id } : {}),
    },
  });
};

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });
    if (resp.data.kind !== "trigger") {
      throw new Error("Unexpected manifest response kind.");
    }
    if (resp.data.operation === "deleted") {
      trigger.value = null;
      loadedYaml.value = "";
      manifestText.value = "";
      store.commit("ui/notification_set", "Trigger deleted.");
      await redirectAfterDelete();
      return;
    }
    const appliedTrigger = resp.data.trigger || null;
    if (appliedTrigger) {
      setLoadedState(appliedTrigger);
      await syncRouteToTrigger(appliedTrigger);
    }
    store.commit("ui/notification_set", `Trigger ${resp.data.operation}.`);
  } catch (error: any) {
    if (error?.message === "Unexpected manifest response kind.") {
      store.commit("ui/notification_set_error", "Manifest apply did not return a trigger payload.");
    } else {
      store.commit("ui/notification_set_error", extractError(error, "Could not apply trigger manifest."));
    }
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchTrigger);

watch(
  () => route.params.trigger_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await fetchTrigger();
  },
);
</script>

<style lang="scss" scoped>
#trigger-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}
</style>
