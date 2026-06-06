<template>
  <div id="item-definition-details">
    <div v-if="isLoading" class="color-text-60">Loading item...</div>
    <template v-else-if="itemDefinition">
      <div class="item-definition-header mb-4">
        <div>
          <h2>{{ itemDefinition.name }}</h2>
          <div class="color-text-60">
            ID: {{ itemDefinition.id }} | Slug: {{ itemDefinition.slug }} | Type: {{ itemDefinition.type }}
          </div>
        </div>

        <div class="item-definition-actions">
          <button class="btn-small" :disabled="!itemDefinition.yaml" @click="copyYaml">
            COPY YAML
          </button>
          <button class="btn-small" :disabled="!itemDefinition.yaml" @click="editYaml">
            EDIT
          </button>
        </div>
      </div>

      <textarea
        :value="itemDefinition.yaml || ''"
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

const itemDefinition = ref<any | null>(null);
const isLoading = ref(false);
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/itemdefinitions/${route.params.item_definition_id}/`
));

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not load item.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not load item.";
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not load item.";
};

const fetchItemDefinition = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    itemDefinition.value = resp.data;
  } catch (error: any) {
    itemDefinition.value = null;
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyYaml = async () => {
  try {
    await navigator.clipboard.writeText(itemDefinition.value?.yaml || "");
    store.commit("ui/notification_set", "Item YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
  }
};

const editYaml = () => {
  if (!itemDefinition.value) return;
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: route.params.world_id,
    },
    query: {
      prefill: "item-definition",
      item_definition_id: itemDefinition.value.id,
    },
  });
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
@import "@/styles/colors.scss";

#item-definition-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;

  .item-definition-header {
    align-items: flex-start;
    display: flex;
    gap: 1rem;
    justify-content: space-between;
  }

  .item-definition-actions {
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
