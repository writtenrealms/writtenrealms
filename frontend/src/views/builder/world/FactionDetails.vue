<template>
  <div id="faction-details">
    <div v-if="isLoading" class="color-text-60">Loading faction...</div>

    <template v-else-if="faction">
      <ManifestYamlEditor
        v-if="!isInstanceWorld"
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        copy-success-message="Faction YAML copied."
        @save="submitManifest"
      >
        <template #header>
          <h2 class="definition-title">{{ faction.name || faction.code }}</h2>
          <div class="definition-meta color-text-60">
            ID: {{ faction.id }} | Code: {{ faction.code }} | {{ formatFactionType(faction.type) }}
          </div>
        </template>
        <template #actions>
          <button class="btn-thin" :disabled="!faction.delete_yaml" @click="copyDeleteYaml">
            COPY DELETE YAML
          </button>
        </template>
      </ManifestYamlEditor>

      <template v-else>
        <section class="faction-header mb-4">
          <h2 class="definition-title">{{ faction.name || faction.code }}</h2>
          <div class="definition-meta color-text-60">
            ID: {{ faction.id }} | Code: {{ faction.code }} | {{ formatFactionType(faction.type) }}
          </div>
          <div class="inherited-notice">
            Factions in instances are inherited from the parent world.
            <router-link
              :to="{ name: 'builder_world_faction_list', params: { world_id: inheritedWorld.id } }"
            >
              Open {{ inheritedWorld.name }} Factions
            </router-link>
          </div>
        </section>

        <textarea
          :value="loadedYaml"
          class="manifest-output"
          readonly
          spellcheck="false"
        />
      </template>
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

const faction = ref<any | null>(null);
const isLoading = ref(false);
const isSubmitting = ref(false);
const manifestText = ref("");
const loadedYaml = ref("");
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => !!inheritedWorld.value.id);
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/factions/${route.params.faction_id}/`
));
const manifestApplyEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);

const formatFactionType = (value) => {
  if (value === "core") return "Core";
  if (value === "reputation") return "Reputation";
  return value || "";
};

const extractError = (error: any, fallbackMessage = "Could not load faction."): string => {
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
  faction.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
};

const fetchFaction = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    setLoadedState(resp.data);
  } catch (error: any) {
    faction.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyDeleteYaml = async () => {
  try {
    await navigator.clipboard.writeText(faction.value?.delete_yaml || "");
    store.commit("ui/notification_set", "Faction delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy delete YAML to clipboard.");
  }
};

const syncRouteToFaction = async (payload: any) => {
  if (!payload?.id || String(route.params.faction_id) === String(payload.id)) return;
  await router.replace({
    name: "builder_world_faction_details",
    params: {
      world_id: route.params.world_id,
      faction_id: payload.id,
    },
  });
};

const redirectAfterDelete = async () => {
  await router.push({
    name: "builder_world_faction_list",
    params: {
      world_id: route.params.world_id,
    },
  });
};

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });
    if (resp.data.kind !== "faction") {
      throw new Error("Unexpected manifest response kind.");
    }
    if (resp.data.operation === "deleted") {
      faction.value = null;
      loadedYaml.value = "";
      manifestText.value = "";
      store.commit("ui/notification_set", "Faction deleted.");
      await redirectAfterDelete();
      return;
    }
    const appliedFaction = resp.data.faction || null;
    if (appliedFaction) {
      setLoadedState(appliedFaction);
      await syncRouteToFaction(appliedFaction);
    }
    store.commit("ui/notification_set", `Faction ${resp.data.operation}.`);
  } catch (error: any) {
    if (error?.message === "Unexpected manifest response kind.") {
      store.commit("ui/notification_set_error", "Manifest apply did not return a faction payload.");
    } else {
      store.commit("ui/notification_set_error", extractError(error, "Could not apply faction manifest."));
    }
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchFaction);

watch(
  () => route.params.faction_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await fetchFaction();
  },
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#faction-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;

  .definition-title {
    margin-bottom: 0.35rem;
  }

  .definition-meta {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .faction-header {
    min-width: 0;
  }

  .inherited-notice {
    color: $color-text-hex-60;
    line-height: 1.4;
  }

  .manifest-output {
    box-sizing: border-box;
    width: 100%;
    min-height: 520px;
    padding: 0.75rem;
    border: 1px solid $color-form-border;
    background: $color-background;
    color: $color-text;
    font-family: monospace;
    line-height: 1.35;
  }
}
</style>
