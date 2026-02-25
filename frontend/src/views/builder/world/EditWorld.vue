<template>
  <div id="edit-world-manifest">
    <h2>{{ world.name.toUpperCase() }} EDIT WORLD</h2>
    <div class="color-text-60 mb-6">
      Paste a YAML manifest and apply it. Supported kinds: worldconfig (update world config) and trigger (create/update/delete triggers).
    </div>

    <textarea
      v-model="manifestText"
      class="manifest-input"
      placeholder="Paste YAML manifest here..."
      spellcheck="false"
    />

    <div class="manifest-actions mt-4">
      <button class="btn-small" :disabled="isSubmitting || !manifestText.trim()" @click="submitManifest">
        APPLY MANIFEST
      </button>
    </div>

    <div v-if="appliedKind && lastOperation" class="manifest-result mt-6 color-text-60">
      <template v-if="appliedKind === 'trigger' && appliedTrigger">
        <template v-if="lastOperation === 'deleted'">
          Deleted {{ appliedTrigger.key }}.
        </template>
        <template v-else>
          {{ capfirst(lastOperation) }} {{ appliedTrigger.key }} ({{ appliedTrigger.scope }} / {{ appliedTrigger.kind }}).
        </template>
      </template>
      <template v-else-if="appliedKind === 'worldconfig'">
        Updated world config for {{ world.name }}.
      </template>
      <template v-else>
        {{ capfirst(lastOperation) }} manifest.
      </template>
    </div>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import { capfirst } from "@/core/utils.ts";

const store = useStore();
const route = useRoute();

const world = computed(() => store.state.builder.world);
const manifestText = ref("");
const isSubmitting = ref(false);
const appliedKind = ref<string>("");
const appliedTrigger = ref<any | null>(null);
const lastOperation = ref<string>("");

const endpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not apply manifest.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not apply manifest.";
  if (typeof data === "object") {
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not apply manifest.";
};

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(endpoint.value, {
      manifest: manifestText.value,
    });
    appliedKind.value = String(resp.data.kind || "").toLowerCase();
    lastOperation.value = String(resp.data.operation || "updated");

    if (appliedKind.value === "trigger") {
      appliedTrigger.value = resp.data.trigger || null;
      manifestText.value = resp.data.trigger?.yaml || manifestText.value;
    } else if (appliedKind.value === "worldconfig") {
      appliedTrigger.value = null;
      manifestText.value = resp.data.world_config?.yaml || manifestText.value;
      await Promise.all([
        store.dispatch("builder/fetch_world", route.params.world_id),
        store.dispatch("builder/worlds/config_fetch", {
          world_id: route.params.world_id,
        }),
      ]);
    } else {
      appliedTrigger.value = null;
    }

    const manifestLabel = appliedKind.value ? `${appliedKind.value} manifest` : "manifest";
    store.commit("ui/notification_set", `${capfirst(manifestLabel)} ${lastOperation.value}.`);
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isSubmitting.value = false;
  }
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#edit-world-manifest {
  .manifest-input {
    width: 100%;
    min-height: 480px;
    padding: 0.75rem;
    border: 1px solid $color-form-border;
    background: $color-background;
    color: $color-text;
    font-family: monospace;
    line-height: 1.35;
  }
}
</style>
