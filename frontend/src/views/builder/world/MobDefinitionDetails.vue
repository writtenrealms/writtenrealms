<template>
  <div id="mob-definition-details">
    <div v-if="isLoading" class="color-text-60">Loading mob...</div>
    <template v-else-if="mobDefinition">
      <div class="mob-definition-header mb-4">
        <div>
          <h2>{{ mobDefinition.name }}</h2>
          <div class="color-text-60">
            ID: {{ mobDefinition.id }} | Slug: {{ mobDefinition.slug }} | Type: {{ mobDefinition.type }}
          </div>
        </div>

        <div class="mob-definition-actions">
          <button class="btn-small" :disabled="!mobDefinition.yaml" @click="copyYaml">
            COPY YAML
          </button>
          <button class="btn-small" :disabled="!mobDefinition.yaml" @click="editYaml">
            EDIT
          </button>
        </div>
      </div>

      <textarea
        :value="mobDefinition.yaml || ''"
        class="manifest-output"
        readonly
        spellcheck="false"
      />
    </template>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";

const route = useRoute();
const router = useRouter();
const store = useStore();

const mobDefinition = ref<any | null>(null);
const isLoading = ref(false);
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/mobdefinitions/${route.params.mob_definition_id}/`
));

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not load mob.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not load mob.";
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not load mob.";
};

const fetchMobDefinition = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    mobDefinition.value = resp.data;
  } catch (error: any) {
    mobDefinition.value = null;
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyYaml = async () => {
  try {
    await navigator.clipboard.writeText(mobDefinition.value?.yaml || "");
    store.commit("ui/notification_set", "Mob YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
  }
};

const editYaml = () => {
  if (!mobDefinition.value) return;
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: route.params.world_id,
    },
    query: {
      prefill: "mob-definition",
      mob_definition_id: mobDefinition.value.id,
    },
  });
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
@import "@/styles/colors.scss";

#mob-definition-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;

  .mob-definition-header {
    align-items: flex-start;
    display: flex;
    gap: 1rem;
    justify-content: space-between;
  }

  .mob-definition-actions {
    display: flex;
    flex-shrink: 0;
    gap: 0.5rem;
  }

  .manifest-output {
    box-sizing: border-box;
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
