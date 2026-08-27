<template>
  <div
    id="zone-config"
    v-if="canConfigureZone"
  >
    <div v-if="isLoading" class="config-state color-text-60" aria-live="polite">
      Loading Zone YAML...
    </div>

    <div v-else-if="loadError" class="config-error" role="alert">
      <div>{{ loadError }}</div>
      <button class="btn-thin" @click="loadZoneYaml()">RETRY</button>
    </div>

    <template v-else>
      <div v-if="submitError" class="config-error submit-error" role="alert">
        {{ submitError }}
      </div>

      <ManifestYamlEditor
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        :min-height="500"
        copy-success-message="Zone YAML copied."
        copy-error-message="Unable to copy Zone YAML to clipboard."
        textarea-label="Zone YAML"
        @save="saveZoneYaml"
      >
        <template #header>
          <h2>{{ zone.name }} Config</h2>
          <div class="zone-meta color-text-60">
            {{ manifestRef }} · Edit this zone's canonical YAML manifest.
          </div>
        </template>

        <template #actions>
          <button
            class="btn-thin"
            :disabled="!deleteYaml"
            @click="copyDeleteYaml"
          >
            COPY DELETE YAML
          </button>
        </template>
      </ManifestYamlEditor>
    </template>
  </div>
  <div v-else>
    You do not have permission to configure this zone.
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import ManifestYamlEditor from "@/components/builder/world/ManifestYamlEditor.vue";
import { applyWorldManifest, manifestApiErrorMessage } from "@/services/manifests";

const route = useRoute();
const store = useStore();

const zone = computed(() => store.state.builder.zone || {});
const routeZoneMatchesStore = computed(() => (
  String(zone.value?.relative_id) === String(route.params.zone_relative_id)
));
const canConfigureZone = computed(() => (
  store.state.builder.world?.builder_info?.builder_rank > 2
  || (
    routeZoneMatchesStore.value
    && zone.value.has_assignment === true
  )
));
const manifestRef = computed(() => `zone@${route.params.zone_relative_id}`);
const manifestText = ref("");
const loadedYaml = ref("");
const deleteYaml = ref("");
const isLoading = ref(true);
const isSubmitting = ref(false);
const loadError = ref("");
const submitError = ref("");

interface ZoneRouteIdentity {
  worldId: string;
  zoneRelativeId: string;
}

interface ZoneLoadOptions {
  afterSave?: boolean;
}

let zoneYamlLoadId = 0;
let zoneYamlSaveId = 0;
let cancelZoneYamlLoad: (() => void) | null = null;

const currentRouteIdentity = (): ZoneRouteIdentity => ({
  worldId: String(route.params.world_id),
  zoneRelativeId: String(route.params.zone_relative_id),
});

const routeStillMatches = (identity: ZoneRouteIdentity): boolean => (
  String(route.params.world_id) === identity.worldId
  && String(route.params.zone_relative_id) === identity.zoneRelativeId
);

const loadStillCurrent = (
  loadId: number,
  identity: ZoneRouteIdentity,
): boolean => loadId === zoneYamlLoadId && routeStillMatches(identity);

const saveStillCurrent = (
  saveId: number,
  identity: ZoneRouteIdentity,
): boolean => saveId === zoneYamlSaveId && routeStillMatches(identity);

const cancelPendingZoneYamlLoad = () => {
  if (!cancelZoneYamlLoad) return;
  cancelZoneYamlLoad();
  cancelZoneYamlLoad = null;
};

const clearLoadedState = () => {
  manifestText.value = "";
  loadedYaml.value = "";
  deleteYaml.value = "";
};

const setLoadedState = (payload: any) => {
  store.commit("builder/zone_set", payload);
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
  deleteYaml.value = payload?.delete_yaml || "";
  loadError.value = "";
};

const loadZoneYaml = async (
  identity = currentRouteIdentity(),
  options: ZoneLoadOptions = {},
): Promise<boolean> => {
  if (!routeStillMatches(identity)) return false;

  const loadId = ++zoneYamlLoadId;
  cancelPendingZoneYamlLoad();
  if (!canConfigureZone.value) {
    if (loadStillCurrent(loadId, identity)) isLoading.value = false;
    return false;
  }

  const afterSave = options.afterSave === true;
  if (!afterSave) {
    clearLoadedState();
    isLoading.value = true;
  }
  loadError.value = "";
  if (!afterSave) submitError.value = "";

  const cancellation = axios.CancelToken.source();
  cancelZoneYamlLoad = cancellation.cancel;

  try {
    const payload = await store.dispatch("builder/zone_relative_fetch", {
      world_id: identity.worldId,
      zone_relative_id: identity.zoneRelativeId,
      cancelToken: cancellation.token,
      commit_zone: false,
      throw_on_error: true,
    });
    if (!loadStillCurrent(loadId, identity)) return false;
    if (!payload?.yaml) throw new Error("Zone detail did not include YAML.");
    if (String(payload?.relative_id) !== identity.zoneRelativeId) {
      throw new Error("Zone detail did not match the current route.");
    }
    setLoadedState(payload);
    return true;
  } catch (error: unknown) {
    if (axios.isCancel(error) || !loadStillCurrent(loadId, identity)) {
      return false;
    }
    const fallback = (
      afterSave
        ? "Zone YAML was saved, but its updated state could not be reloaded. Refresh this page to try again."
        : error instanceof Error && error.message === "Zone detail did not include YAML."
        ? error.message
        : "Could not load Zone YAML."
    );
    const message = manifestApiErrorMessage(error, fallback);
    if (afterSave) submitError.value = message;
    else loadError.value = message;
    store.commit("ui/notification_set_error", message);
    return false;
  } finally {
    if (loadStillCurrent(loadId, identity)) {
      cancelZoneYamlLoad = null;
      if (!afterSave) isLoading.value = false;
    }
  }
};

const saveZoneYaml = async () => {
  const saveRoute = currentRouteIdentity();
  const saveId = ++zoneYamlSaveId;
  const submittedYaml = manifestText.value;
  isSubmitting.value = true;
  submitError.value = "";

  try {
    const response = await applyWorldManifest(
      saveRoute.worldId,
      submittedYaml,
      "zone",
      `zone@${saveRoute.zoneRelativeId}`,
      "apply",
      "updated",
    );
    if (!saveStillCurrent(saveId, saveRoute)) return;
    if (response.kind !== "zone" || response.operation !== "updated") {
      throw new Error("Unexpected zone manifest response.");
    }

    const reloaded = await loadZoneYaml(saveRoute, {
      afterSave: true,
    });
    if (!saveStillCurrent(saveId, saveRoute)) return;
    if (reloaded) store.commit("ui/notification_set", "Zone YAML saved.");
  } catch (error: unknown) {
    if (!saveStillCurrent(saveId, saveRoute)) return;
    const fallback = (
      error instanceof Error
      && error.message === "Unexpected zone manifest response."
    )
      ? error.message
      : "Could not save Zone YAML.";
    submitError.value = manifestApiErrorMessage(error, fallback);
    store.commit("ui/notification_set_error", submitError.value);
  } finally {
    if (saveStillCurrent(saveId, saveRoute)) isSubmitting.value = false;
  }
};

const copyDeleteYaml = async () => {
  try {
    await navigator.clipboard.writeText(deleteYaml.value);
    store.commit("ui/notification_set", "Zone delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy Zone delete YAML to clipboard.");
  }
};

watch(
  canConfigureZone,
  async (canConfigure, previouslyCouldConfigure) => {
    if (!canConfigure) {
      ++zoneYamlLoadId;
      ++zoneYamlSaveId;
      cancelPendingZoneYamlLoad();
      isSubmitting.value = false;
      isLoading.value = false;
      clearLoadedState();
      return;
    }
    if (previouslyCouldConfigure === true) return;
    await loadZoneYaml(currentRouteIdentity());
  },
  { immediate: true },
);

watch(
  () => [route.params.world_id, route.params.zone_relative_id],
  async (nextValue, previousValue) => {
    if (
      String(nextValue[0]) === String(previousValue[0])
      && String(nextValue[1]) === String(previousValue[1])
    ) return;

    ++zoneYamlLoadId;
    ++zoneYamlSaveId;
    cancelPendingZoneYamlLoad();
    isSubmitting.value = false;
    isLoading.value = true;
    loadError.value = "";
    submitError.value = "";
    clearLoadedState();
    await loadZoneYaml(currentRouteIdentity());
  },
);

onBeforeUnmount(() => {
  ++zoneYamlLoadId;
  ++zoneYamlSaveId;
  cancelPendingZoneYamlLoad();
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

#zone-config {
  box-sizing: border-box;
  max-width: $site-max-width;
  min-width: 0;
  width: 100%;
}

.zone-meta {
  min-width: 0;
  overflow-wrap: anywhere;
}

.config-state {
  align-items: center;
  display: flex;
  justify-content: center;
  min-height: 12rem;
}

.config-error {
  border: 1px solid $color-form-border;
  padding: 1rem;

  .btn-thin {
    margin-top: 0.75rem;
  }
}

.submit-error {
  margin-bottom: 1rem;
}
</style>
