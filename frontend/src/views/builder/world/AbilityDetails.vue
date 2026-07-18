<template>
  <div id="ability-details">
    <div v-if="isLoading" class="color-text-60">Loading ability...</div>

    <template v-else-if="ability">
      <ManifestYamlEditor
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        :disabled="isInstanceWorld"
        copy-success-message="Ability YAML copied."
        @save="submitManifest"
      >
        <template #header>
          <h2 class="definition-title">{{ ability.name || ability.slug }}</h2>
          <div class="definition-meta-row">
            <div class="definition-meta color-text-60">
              {{ ability.id }} - {{ ability.slug }}
            </div>
          </div>
          <div v-if="isInstanceWorld" class="inherited-notice">
            Abilities in instances are inherited from the parent world.
            <router-link
              :to="{ name: 'builder_world_ability_list', params: { world_id: inheritedWorld.id } }"
            >
              Open {{ inheritedWorld.name }} Abilities
            </router-link>
          </div>
        </template>
        <template #actions>
          <button
            v-if="!isInstanceWorld"
            class="btn-thin"
            :disabled="!ability.delete_yaml"
            @click="copyDeleteYaml"
          >
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

const ability = ref<any | null>(null);
const isLoading = ref(false);
const isSubmitting = ref(false);
const manifestText = ref("");
const loadedYaml = ref("");
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => !!inheritedWorld.value.id);
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/abilities/${route.params.ability_id}/`
));
const manifestApplyEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);

const extractError = (error: any, fallbackMessage = "Could not load ability."): string => {
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
  ability.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
};

const fetchAbility = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    setLoadedState(resp.data);
  } catch (error: any) {
    ability.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyDeleteYaml = async () => {
  try {
    await navigator.clipboard.writeText(ability.value?.delete_yaml || "");
    store.commit("ui/notification_set", "Ability delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy delete YAML to clipboard.");
  }
};

const syncRouteToAbility = async (payload: any) => {
  const id = payload?.id;
  if (!id || String(route.params.ability_id) === String(id)) return;
  await router.replace({
    name: "builder_world_ability_details",
    params: {
      world_id: route.params.world_id,
      ability_id: id,
    },
  });
};

const submitManifest = async () => {
  if (isInstanceWorld.value) return;
  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });
    if (resp.data.kind !== "ability") {
      throw new Error("Unexpected manifest response kind.");
    }
    if (resp.data.operation === "deleted") {
      ability.value = null;
      loadedYaml.value = "";
      manifestText.value = "";
      store.commit("ui/notification_set", "Ability deleted.");
      await router.push({
        name: "builder_world_ability_list",
        params: {
          world_id: route.params.world_id,
        },
      });
      return;
    }
    const appliedAbility = resp.data.ability || null;
    if (appliedAbility) {
      setLoadedState(appliedAbility);
      await syncRouteToAbility(appliedAbility);
    }
    store.commit("ui/notification_set", `Ability ${resp.data.operation}.`);
  } catch (error: any) {
    if (error?.message === "Unexpected manifest response kind.") {
      store.commit("ui/notification_set_error", "Manifest apply did not return an ability payload.");
    } else {
      store.commit("ui/notification_set_error", extractError(error, "Could not apply ability manifest."));
    }
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchAbility);

watch(
  () => route.params.ability_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await fetchAbility();
  },
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#ability-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}

.definition-title {
  margin-bottom: 0.35rem;
}

.definition-meta-row {
  align-items: center;
  display: flex;
  gap: 0.75rem;
  justify-content: space-between;
  min-width: 0;
  width: 100%;
}

.definition-meta {
  min-width: 0;
  overflow-wrap: anywhere;
}

.inherited-notice {
  color: $color-text-hex-60;
  line-height: 1.4;
  margin-top: 0.5rem;
}
</style>
