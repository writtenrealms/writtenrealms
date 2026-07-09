<template>
  <div id="spawn-plan-details">
    <div v-if="isLoading" class="color-text-60">Loading spawn plan...</div>
    <ManifestYamlEditor
      v-else
      v-model="manifestText"
      :loaded-value="loadedYaml"
      :is-submitting="isSubmitting"
      copy-success-message="Spawn plan YAML copied."
      @save="submitManifest"
    >
      <template #header>
        <h2 class="definition-title">{{ headerTitle }}</h2>
        <div class="definition-meta color-text-60">{{ headerMeta }}</div>
      </template>
      <template #actions>
        <button
          v-if="!isNew"
          class="btn-thin"
          :disabled="!spawnPlan?.delete_yaml"
          @click="copyDeleteYaml"
        >
          COPY DELETE YAML
        </button>
      </template>
    </ManifestYamlEditor>
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

const spawnPlan = ref<any | null>(null);
const isLoading = ref(false);
const isSubmitting = ref(false);
const manifestText = ref("");
const loadedYaml = ref("");

const isNew = computed(() => String(route.params.spawn_plan_id) === "new");
const zone = computed(() => store.state.builder.zone);
const zoneManifestRef = computed(() => {
  if (zone.value?.manifest_ref) return zone.value.manifest_ref;
  if (zone.value?.relative_id) return `zone@${zone.value.relative_id}`;
  return "zone@ZONE_RELATIVE_ID";
});
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/zones/${route.params.zone_id}/spawn-plans/${route.params.spawn_plan_id}/`
));
const manifestApplyEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);
const headerTitle = computed(() => spawnPlan.value?.name || "New Spawn Plan");
const headerMeta = computed(() => {
  if (spawnPlan.value) {
    return `${spawnPlan.value.id} - ${spawnPlan.value.slug} - ${spawnPlan.value.zone_ref}`;
  }
  return `New spawn plan for ${zoneManifestRef.value}`;
});

const extractError = (error: any, fallbackMessage = "Could not load spawn plan."): string => {
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

const slugifyName = (value: string): string => {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "new-spawn-plan";
};

const newSpawnPlanYaml = () => {
  const zoneName = zone.value?.name || "Zone";
  const name = `${zoneName} Spawn Plan`;
  const slug = slugifyName(name);
  return `kind: spawnplan
metadata:
  slug: ${slug}
  name: ${JSON.stringify(name)}
spec:
  zone: ${zoneManifestRef.value}
  respawn:
    mode: fixed
    seconds: 300
  entries: []
`;
};

const ensureRouteZone = async () => {
  if (String(zone.value?.id || "") === String(route.params.zone_id)) return;
  await store.dispatch("builder/zone_fetch", {
    world_id: route.params.world_id,
    zone_id: route.params.zone_id,
  });
};

const setLoadedState = (payload: any) => {
  spawnPlan.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
};

const setNewState = async () => {
  spawnPlan.value = null;
  await ensureRouteZone();
  const yaml = newSpawnPlanYaml();
  loadedYaml.value = "";
  manifestText.value = yaml;
};

const fetchSpawnPlan = async () => {
  if (isNew.value) {
    await setNewState();
    return;
  }
  isLoading.value = true;
  try {
    await ensureRouteZone();
    const resp = await axios.get(endpoint.value);
    setLoadedState(resp.data);
  } catch (error: any) {
    spawnPlan.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyDeleteYaml = async () => {
  try {
    await navigator.clipboard.writeText(spawnPlan.value?.delete_yaml || "");
    store.commit("ui/notification_set", "Spawn plan delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy delete YAML to clipboard.");
  }
};

const syncRouteToSpawnPlan = async (payload: any) => {
  const id = payload?.id;
  if (!id) return;
  const targetZoneId = payload?.zone?.id || route.params.zone_id;
  if (
    String(route.params.spawn_plan_id) === String(id)
    && String(route.params.zone_id) === String(targetZoneId)
  ) {
    return;
  }
  await router.replace({
    name: "builder_zone_spawn_plan_details",
    params: {
      world_id: route.params.world_id,
      zone_id: targetZoneId,
      spawn_plan_id: id,
    },
  });
};

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });
    if (resp.data.kind !== "spawnplan") {
      throw new Error("Unexpected manifest response kind.");
    }
    if (resp.data.operation === "deleted") {
      const deletedName = resp.data.spawn_plan?.name || "Spawn plan";
      spawnPlan.value = null;
      loadedYaml.value = "";
      manifestText.value = "";
      store.commit("ui/notification_set", `${deletedName} deleted.`);
      await router.push({
        name: "builder_zone_spawn_plan_list",
        params: {
          world_id: route.params.world_id,
          zone_id: route.params.zone_id,
        },
      });
      return;
    }
    const appliedPlan = resp.data.spawn_plan || null;
    if (appliedPlan) {
      setLoadedState(appliedPlan);
      await syncRouteToSpawnPlan(appliedPlan);
    }
    store.commit("ui/notification_set", `Spawn plan ${resp.data.operation}.`);
  } catch (error: any) {
    if (error?.message === "Unexpected manifest response kind.") {
      store.commit("ui/notification_set_error", "Manifest apply did not return a spawn plan payload.");
    } else {
      store.commit("ui/notification_set_error", extractError(error, "Could not apply spawn plan manifest."));
    }
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchSpawnPlan);

watch(
  () => [route.params.zone_id, route.params.spawn_plan_id],
  async (nextValue, prevValue) => {
    if (String(nextValue) === String(prevValue)) return;
    await fetchSpawnPlan();
  },
);
</script>

<style lang="scss" scoped>
#spawn-plan-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}

.definition-title {
  margin-bottom: 0.35rem;
}

.definition-meta {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
