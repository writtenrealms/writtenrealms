<template>
  <div id="ability-details">
    <div v-if="isLoading" class="color-text-60">Loading ability...</div>

    <template v-else-if="ability">
      <div class="ability-header mb-4">
        <div>
          <h2>{{ ability.name || ability.slug }}</h2>
          <div class="color-text-60">
            ID: {{ ability.id }} | Slug: {{ ability.slug }} | {{ formatActionType(ability.action_type) }}
          </div>
        </div>

        <div class="ability-actions">
          <button class="btn-small" :disabled="!ability.yaml" @click="copyYaml">
            COPY YAML
          </button>
          <button class="btn-small" :disabled="!ability.yaml" @click="editYaml">
            EDIT
          </button>
          <button class="btn-thin" :disabled="!ability.delete_yaml" @click="copyDeleteYaml">
            COPY DELETE YAML
          </button>
        </div>
      </div>

      <section class="ability-summary mb-4">
        <div class="summary-row">
          <div class="summary-label">Status</div>
          <div>{{ ability.is_active ? "Active" : "Inactive" }}</div>
        </div>
        <div class="summary-row">
          <div class="summary-label">Commands</div>
          <ManifestValue :value="ability.command_verbs || []" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Target</div>
          <ManifestValue :value="ability.target || {}" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Availability</div>
          <ManifestValue :value="ability.availability || {}" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Requirements</div>
          <ManifestValue :value="ability.requirements || {}" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Cost</div>
          <ManifestValue :value="ability.cost || {}" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Cast Time</div>
          <ManifestValue :value="ability.cast_time || {}" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Cooldown</div>
          <ManifestValue :value="ability.cooldown || {}" />
        </div>
        <div class="summary-row full">
          <div class="summary-label">Components</div>
          <ManifestValue :value="ability.components || []" :collapse-complex="true" />
        </div>
      </section>

      <textarea
        :value="ability.yaml || ''"
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
import ManifestValue from "@/components/builder/world/ManifestValue.vue";

const route = useRoute();
const router = useRouter();
const store = useStore();

const ability = ref<any | null>(null);
const isLoading = ref(false);
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/abilities/${route.params.ability_id}/`
));

const formatActionType = (value) => {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not load ability.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not load ability.";
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not load ability.";
};

const fetchAbility = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    ability.value = resp.data;
  } catch (error: any) {
    ability.value = null;
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyYaml = async () => {
  try {
    await navigator.clipboard.writeText(ability.value?.yaml || "");
    store.commit("ui/notification_set", "Ability YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
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

const editYaml = () => {
  if (!ability.value) return;
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: route.params.world_id,
    },
    query: {
      prefill: "ability",
      ability_id: ability.value.id,
    },
  });
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
@import "@/styles/layout.scss";

#ability-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;

  .ability-header {
    align-items: flex-start;
    display: flex;
    gap: 1rem;
    justify-content: space-between;

    @media ($mobile-site) {
      flex-direction: column;
    }
  }

  .ability-actions {
    display: flex;
    flex-shrink: 0;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .ability-summary {
    border-top: 1px solid $color-background-light-border;
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    max-width: 960px;
    padding-top: 1rem;
  }

  .summary-row {
    min-width: 0;

    &.full {
      grid-column: 1 / -1;
    }
  }

  .summary-label {
    color: $color-text-hex-60;
    margin-bottom: 0.35rem;
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
