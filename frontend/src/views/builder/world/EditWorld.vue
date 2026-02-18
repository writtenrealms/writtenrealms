<template>
  <div id="edit-world-manifest">
    <h2>{{ world.name.toUpperCase() }} EDIT WORLD</h2>
    <div class="color-text-60 mb-6">
      Paste a YAML manifest and apply it. Trigger manifests can create, update, or delete trigger definitions in this world.
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

    <div v-if="appliedTrigger && lastOperation" class="manifest-result mt-6 color-text-60">
      <template v-if="lastOperation === 'deleted'">
        Deleted {{ appliedTrigger.key }}.
      </template>
      <template v-else>
        {{ capfirst(lastOperation) }} {{ appliedTrigger.key }} ({{ appliedTrigger.scope }} / {{ appliedTrigger.kind }}).
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
    appliedTrigger.value = resp.data.trigger;
    lastOperation.value = String(resp.data.operation || "updated");
    manifestText.value = resp.data.trigger?.yaml || manifestText.value;
    store.commit("ui/notification_set", `Manifest ${lastOperation.value}.`);
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
