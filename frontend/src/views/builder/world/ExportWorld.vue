<template>
  <div id="world-export">
    <h2>{{ world.name.toUpperCase() }} EXPORT</h2>
    <div class="color-text-60 mb-6">
      This export is a multi-document YAML stream. Each block can be pasted into <strong>World &gt; Edit World</strong>, and documents are applied in order.
    </div>

    <div v-if="summary" class="color-text-60 mb-4">
      {{ summary.documents }} documents: {{ summary.currencies }} currencies, {{ summary.zones }} zones, {{ summary.rooms }} rooms, {{ summary.item_templates }} item templates, {{ summary.item_definitions }} items, {{ summary.item_bundles }} item bundles, {{ summary.mob_templates }} mob templates, {{ summary.mob_definitions }} mobs, {{ summary.quest_arcs }} quest arcs, {{ summary.quests }} quests, and {{ summary.triggers }} triggers.
    </div>

    <div class="manifest-actions mb-4">
      <button class="btn-small" :disabled="isLoading || !yamlText" @click="copyYaml">
        COPY YAML
      </button>
      <button class="btn-thin ml-2" :disabled="isLoading" @click="fetchExport">
        REFRESH
      </button>
    </div>

    <textarea
      :value="yamlText"
      class="manifest-output"
      readonly
      spellcheck="false"
    />
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";

const store = useStore();
const route = useRoute();

const world = computed(() => store.state.builder.world);
const yamlText = ref("");
const summary = ref<any | null>(null);
const isLoading = ref(false);

const endpoint = computed(() => `/builder/worlds/${route.params.world_id}/export/`);

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not load world export.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not load world export.";
  if (typeof data === "object") {
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not load world export.";
};

const fetchExport = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    yamlText.value = resp.data.yaml || "";
    summary.value = resp.data.summary || null;
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyYaml = async () => {
  if (!yamlText.value) return;
  await navigator.clipboard.writeText(yamlText.value);
  store.commit("ui/notification_set", "World export YAML copied.");
};

onMounted(fetchExport);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#world-export {
  .manifest-output {
    width: 100%;
    min-height: 640px;
    padding: 0.75rem;
    border: 1px solid $color-form-border;
    background: $color-background;
    color: $color-text;
    font-family: monospace;
    line-height: 1.35;
  }
}
</style>
