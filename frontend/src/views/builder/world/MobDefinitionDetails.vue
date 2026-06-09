<template>
  <div id="mob-definition-details">
    <div v-if="isLoading" class="color-text-60">Loading mob...</div>
    <template v-else-if="mobDefinition">
      <ManifestYamlEditor
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        copy-success-message="Mob YAML copied."
        @save="submitManifest"
      >
        <template #header>
          <h2>{{ mobDefinition.name }}</h2>
          <div class="color-text-60">
            ID: {{ mobDefinition.id }} <span class='mx-2'>|</span> Slug: {{ mobDefinition.slug }}
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

const route = useRoute();
const router = useRouter();
const store = useStore();

const mobDefinition = ref<any | null>(null);
const isLoading = ref(false);
const isSubmitting = ref(false);
const manifestText = ref("");
const loadedYaml = ref("");
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/mobdefinitions/${route.params.mob_definition_id}/`
));
const manifestApplyEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);

const extractError = (error: any, fallbackMessage = "Could not load mob."): string => {
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
  mobDefinition.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
};

const fetchMobDefinition = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    setLoadedState(resp.data);
  } catch (error: any) {
    mobDefinition.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const syncRouteToMob = async (payload: any) => {
  const id = payload?.id;
  if (!id || String(route.params.mob_definition_id) === String(id)) return;
  await router.replace({
    name: "builder_mob_definition_details",
    params: {
      world_id: route.params.world_id,
      mob_definition_id: id,
    },
  });
};

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });
    if (resp.data.kind !== "mobdefinition") {
      throw new Error("Unexpected manifest response kind.");
    }
    if (resp.data.operation === "deleted") {
      mobDefinition.value = null;
      loadedYaml.value = "";
      manifestText.value = "";
      store.commit("ui/notification_set", "Mob definition deleted.");
      await router.push({
        name: "builder_mob_definition_list",
        params: {
          world_id: route.params.world_id,
        },
      });
      return;
    }
    const appliedMob = resp.data.mob_definition || null;
    if (appliedMob) {
      setLoadedState(appliedMob);
      await syncRouteToMob(appliedMob);
    }
    store.commit("ui/notification_set", `Mob definition ${resp.data.operation}.`);
  } catch (error: any) {
    if (error?.message === "Unexpected manifest response kind.") {
      store.commit("ui/notification_set_error", "Manifest apply did not return a mob payload.");
    } else {
      store.commit("ui/notification_set_error", extractError(error, "Could not apply mob manifest."));
    }
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchMobDefinition);

watch(
  () => route.params.mob_definition_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await fetchMobDefinition();
  },
);
</script>

<style lang="scss" scoped>
#mob-definition-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}
</style>
