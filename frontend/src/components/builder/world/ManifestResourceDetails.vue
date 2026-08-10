<template>
  <div class="manifest-resource-details">
    <div v-if="isLoading" class="resource-state color-text-60" aria-live="polite">
      Loading {{ resourceLabel }}...
    </div>

    <div v-else-if="loadError" class="resource-error" role="alert">
      <div>{{ loadError }}</div>
      <button class="btn-thin" @click="fetchResource">RETRY</button>
    </div>

    <div v-else-if="!resource" class="resource-state color-text-60">
      This {{ resourceLabel }} is unavailable.
    </div>

    <template v-else-if="isReadOnly">
      <section class="readonly-header mb-4">
        <slot name="header" :resource="resource" />
        <slot name="summary" :resource="resource" />

        <div v-if="isInherited" class="readonly-notice">
          {{ listLabel }} in instances are inherited from the parent world.
          <router-link :to="{ name: listRouteName, params: { world_id: inheritedWorld?.id } }">
            Open {{ inheritedWorld?.name }} {{ listLabel }}
          </router-link>
        </div>
        <div v-else class="readonly-notice">
          You can view this {{ resourceLabel }}, but your builder role cannot edit it.
        </div>

        <button class="btn-small readonly-copy" :disabled="!loadedYaml" @click="copyYaml">
          COPY YAML
        </button>
      </section>

      <textarea
        :value="loadedYaml"
        class="manifest-output"
        readonly
        spellcheck="false"
      />
    </template>

    <template v-else>
      <div v-if="submitError" class="resource-error submit-error" role="alert">
        {{ submitError }}
      </div>

      <ManifestYamlEditor
        v-model="manifestText"
        :loaded-value="loadedYaml"
        :is-submitting="isSubmitting"
        :copy-success-message="`${resourceTitle} YAML copied.`"
        @save="submitManifest"
      >
        <template #header>
          <slot name="header" :resource="resource" />
          <slot name="summary" :resource="resource" />
        </template>

        <template #actions>
          <button class="btn-thin" :disabled="!resource.delete_yaml" @click="copyDeleteYaml">
            COPY DELETE YAML
          </button>
        </template>
      </ManifestYamlEditor>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useStore } from "vuex";
import ManifestYamlEditor from "@/components/builder/world/ManifestYamlEditor.vue";
import {
  applyWorldManifest,
  manifestApiErrorMessage,
  type BuilderEntityId,
  type BuilderWorldId,
  type ManifestApplyResponse,
  type ManifestResourceKind,
  type ManifestResourceResponseField,
} from "@/services/manifests";

const props = defineProps<{
  worldId: BuilderWorldId;
  resourceId: BuilderEntityId;
  resourceLabel: string;
  resourceTitle: string;
  listLabel: string;
  expectedKind: ManifestResourceKind;
  responseField: ManifestResourceResponseField;
  listRouteName: string;
  detailRouteName: string;
  detailIdParam: string;
  loadResource: () => Promise<any>;
  inheritedWorld?: { id?: BuilderWorldId; name?: string };
}>();

const router = useRouter();
const store = useStore();
const resource = ref<any | null>(null);
const isLoading = ref(true);
const isSubmitting = ref(false);
const loadError = ref("");
const submitError = ref("");
const loadedYaml = ref("");
const manifestText = ref("");
let requestNumber = 0;

const isInherited = computed(() => Boolean(props.inheritedWorld?.id));
const builderRank = computed(() => Number(
  store.state.builder.world?.builder_info?.builder_rank || 0,
));
const isReadOnly = computed(() => isInherited.value || builderRank.value <= 2);

const setLoadedState = (payload: any) => {
  resource.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
  loadError.value = "";
};

const fetchResource = async () => {
  const activeRequest = ++requestNumber;
  isLoading.value = true;
  loadError.value = "";
  submitError.value = "";

  try {
    const payload = await props.loadResource();
    if (activeRequest !== requestNumber) return;
    setLoadedState(payload);
  } catch (error: unknown) {
    if (activeRequest !== requestNumber) return;
    resource.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    loadError.value = manifestApiErrorMessage(
      error,
      `Could not load ${props.resourceLabel}.`,
    );
  } finally {
    if (activeRequest === requestNumber) isLoading.value = false;
  }
};

const copyText = async (value: string, successMessage: string, errorMessage: string) => {
  try {
    await navigator.clipboard.writeText(value);
    store.commit("ui/notification_set", successMessage);
  } catch {
    store.commit("ui/notification_set_error", errorMessage);
  }
};

const copyYaml = () => copyText(
  loadedYaml.value,
  `${props.resourceTitle} YAML copied.`,
  `Unable to copy ${props.resourceLabel} YAML to clipboard.`,
);

const copyDeleteYaml = () => copyText(
  resource.value?.delete_yaml || "",
  `${props.resourceTitle} delete YAML copied.`,
  `Unable to copy ${props.resourceLabel} delete YAML to clipboard.`,
);

const responseResource = (response: ManifestApplyResponse) => {
  const payload = response[props.responseField];
  return payload && typeof payload === "object" ? payload : null;
};

const syncRoute = async (payload: any) => {
  if (!payload?.id || String(props.resourceId) === String(payload.id)) return;
  await router.replace({
    name: props.detailRouteName,
    params: {
      world_id: props.worldId,
      [props.detailIdParam]: payload.id,
    },
  });
};

const submitManifest = async () => {
  isSubmitting.value = true;
  submitError.value = "";

  try {
    const response = await applyWorldManifest(
      props.worldId,
      manifestText.value,
      props.expectedKind,
    );
    if (response.kind !== props.expectedKind) {
      throw new Error("Unexpected manifest response kind.");
    }

    if (response.operation === "deleted") {
      resource.value = null;
      loadedYaml.value = "";
      manifestText.value = "";
      store.commit("ui/notification_set", `${props.resourceTitle} deleted.`);
      await router.push({
        name: props.listRouteName,
        params: { world_id: props.worldId },
      });
      return;
    }

    const appliedResource = responseResource(response);
    if (!appliedResource) {
      throw new Error(`Manifest response did not include the updated ${props.resourceLabel}.`);
    }

    setLoadedState(appliedResource);
    await syncRoute(appliedResource);
    store.commit("ui/notification_set", `${props.resourceTitle} ${response.operation}.`);
  } catch (error: unknown) {
    const isInternalManifestError = error instanceof Error && (
      error.message === "Unexpected manifest response kind."
      || error.message.startsWith("Manifest response did not include")
    );
    const fallback = isInternalManifestError
      ? error.message
      : `Could not apply ${props.resourceLabel} manifest.`;
    submitError.value = manifestApiErrorMessage(error, fallback);
    store.commit("ui/notification_set_error", submitError.value);
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(fetchResource);

watch(() => props.resourceId, (nextValue, previousValue) => {
  if (nextValue !== previousValue) fetchResource();
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.manifest-resource-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}

.resource-state {
  align-items: center;
  display: flex;
  justify-content: center;
  min-height: 12rem;
}

.resource-error {
  border: 1px solid $color-form-border;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem;
}

.submit-error {
  margin-bottom: 1rem;
}

.readonly-header {
  min-width: 0;
}

.readonly-notice {
  color: $color-text-hex-60;
  line-height: 1.4;
  margin-top: 0.75rem;
}

.readonly-copy {
  margin-top: 0.75rem;
}

.manifest-output {
  background: $color-background;
  border: 1px solid $color-form-border;
  box-sizing: border-box;
  color: $color-text;
  font-family: monospace;
  line-height: 1.35;
  min-height: 520px;
  padding: 0.75rem;
  width: 100%;
}
</style>
