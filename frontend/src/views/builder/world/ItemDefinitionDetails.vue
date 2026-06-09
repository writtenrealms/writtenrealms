<template>
  <div id="item-definition-details">
    <div v-if="isLoading" class="color-text-60">Loading item...</div>
    <template v-else-if="itemDefinition">
      <ManifestYamlEditor
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        copy-success-message="Item YAML copied."
        @save="submitManifest"
      >
        <template #header>
          <h2>{{ itemDefinition.name }}</h2>
          <div class="color-text-60">
            ID: {{ itemDefinition.id }} | Slug: {{ itemDefinition.slug }} | Type: {{ itemDefinition.type }}
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

const itemDefinition = ref<any | null>(null);
const isLoading = ref(false);
const isSubmitting = ref(false);
const manifestText = ref("");
const loadedYaml = ref("");
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/itemdefinitions/${route.params.item_definition_id}/`
));
const manifestApplyEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);

const extractError = (error: any, fallbackMessage = "Could not load item."): string => {
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
  itemDefinition.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
};

const fetchItemDefinition = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    setLoadedState(resp.data);
  } catch (error: any) {
    itemDefinition.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const syncRouteToItem = async (payload: any) => {
  const id = payload?.id;
  if (!id || String(route.params.item_definition_id) === String(id)) return;
  await router.replace({
    name: "builder_item_definition_details",
    params: {
      world_id: route.params.world_id,
      item_definition_id: id,
    },
  });
};

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });
    if (resp.data.kind !== "itemdefinition") {
      throw new Error("Unexpected manifest response kind.");
    }
    if (resp.data.operation === "deleted") {
      itemDefinition.value = null;
      loadedYaml.value = "";
      manifestText.value = "";
      store.commit("ui/notification_set", "Item definition deleted.");
      await router.push({
        name: "builder_item_definition_list",
        params: {
          world_id: route.params.world_id,
        },
      });
      return;
    }
    const appliedItem = resp.data.item_definition || null;
    if (appliedItem) {
      setLoadedState(appliedItem);
      await syncRouteToItem(appliedItem);
    }
    store.commit("ui/notification_set", `Item definition ${resp.data.operation}.`);
  } catch (error: any) {
    if (error?.message === "Unexpected manifest response kind.") {
      store.commit("ui/notification_set_error", "Manifest apply did not return an item payload.");
    } else {
      store.commit("ui/notification_set_error", extractError(error, "Could not apply item manifest."));
    }
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchItemDefinition);

watch(
  () => route.params.item_definition_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await fetchItemDefinition();
  },
);
</script>

<style lang="scss" scoped>
#item-definition-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}
</style>
