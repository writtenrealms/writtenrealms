<template>
  <div id="edit-world-manifest">
    <h2>{{ world.name.toUpperCase() }} EDIT WORLD</h2>
    <div class="color-text-60 mb-6">
      Paste one or more YAML manifests. Each YAML document is applied in order. Supported kinds: world, currency, zone, room, itemtemplate, itemdefinition, itembundle, mobtemplate, mobdefinition, ability, abilities, quest, questarc, and trigger.
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
      <template v-if="appliedKind === 'batch' && appliedBatchSummary">
        Applied {{ appliedBatchSummary.documents }} documents<span v-if="batchSummaryText">: {{ batchSummaryText }}</span>.
      </template>
      <template v-else-if="appliedKind === 'trigger' && appliedTrigger">
        <template v-if="lastOperation === 'deleted'">
          Deleted {{ appliedTrigger.key }}.
        </template>
        <template v-else>
          {{ capfirst(lastOperation) }} {{ appliedTrigger.key }} ({{ appliedTrigger.scope }} / {{ appliedTrigger.kind }}).
        </template>
      </template>
      <template v-else-if="appliedKind === 'world'">
        Updated world config for {{ world.name }}.
      </template>
      <template v-else-if="appliedKind === 'itemtemplate' && appliedItemTemplate">
        {{ capfirst(lastOperation) }} {{ appliedItemTemplate.slug || appliedItemTemplate.name }}.
      </template>
      <template v-else-if="appliedKind === 'itemdefinition' && appliedItemDefinition">
        {{ capfirst(lastOperation) }} {{ appliedItemDefinition.slug || appliedItemDefinition.name }}.
      </template>
      <template v-else-if="appliedKind === 'mobdefinition' && appliedMobDefinition">
        {{ capfirst(lastOperation) }} {{ appliedMobDefinition.slug || appliedMobDefinition.name }}.
      </template>
      <template v-else>
        {{ capfirst(lastOperation) }} manifest.
      </template>
    </div>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref } from "vue";
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
const appliedItemTemplate = ref<any | null>(null);
const appliedItemDefinition = ref<any | null>(null);
const appliedMobDefinition = ref<any | null>(null);
const appliedBatchSummary = ref<any | null>(null);
const lastOperation = ref<string>("");

const endpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);
const batchSummaryText = computed(() => {
  const kinds = appliedBatchSummary.value?.kinds || {};
  const labels = Object.entries(kinds).map(([kind, count]) => {
    const suffix = Number(count) === 1 ? "" : "s";
    return `${count} ${kind}${suffix}`;
  });
  return labels.join(", ");
});

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

const loadWorldConfigYaml = async () => {
  let payload = store.state.builder.worlds.config;
  if (!payload?.yaml) {
    payload = await store.dispatch("builder/worlds/config_fetch", {
      world_id: route.params.world_id,
    });
  }
  manifestText.value = payload?.yaml || "";
};

const newItemDefinitionYaml = `kind: itemdefinition
metadata:
  slug: new-item
  name: a new item
spec:
  type: inert
`;

const newMobDefinitionYaml = `kind: mobdefinition
metadata:
  slug: new-mob
  name: a new mob
spec:
  description: ''
  room_description: ''
  notes: ''
  keywords: ''
  type: beast
  assists: false
  level: 1
  exp_worth: 1
  gold: 0
  health_max: 30
  health_regen: 0
  energy_max: 1
  energy_regen: 0
  stamina_max: 50
  stamina_regen: 0
  regen_rate: 4
  attack_power: 1
  ability_power: 0
  armor: 0
  dodge: 0
  crit: 0
  resilience: 0
  fights_back: true
  is_invisible: false
  attributes: {}
  randomization:
    attributes: []
`;

const loadItemDefinitionYaml = async () => {
  const rawItemDefinitionId = route.query.item_definition_id;
  const itemDefinitionId = Array.isArray(rawItemDefinitionId)
    ? rawItemDefinitionId[0]
    : rawItemDefinitionId;
  if (!itemDefinitionId) {
    manifestText.value = "";
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/itemdefinitions/${itemDefinitionId}/`
    );
    manifestText.value = resp.data?.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

const loadMobDefinitionYaml = async () => {
  const rawMobDefinitionId = route.query.mob_definition_id;
  const mobDefinitionId = Array.isArray(rawMobDefinitionId)
    ? rawMobDefinitionId[0]
    : rawMobDefinitionId;
  if (!mobDefinitionId) {
    manifestText.value = "";
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/mobdefinitions/${mobDefinitionId}/`
    );
    manifestText.value = resp.data?.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

onMounted(async () => {
  if (route.query.prefill === "world-config") {
    await loadWorldConfigYaml();
  } else if (route.query.prefill === "item-definition") {
    await loadItemDefinitionYaml();
  } else if (route.query.prefill === "new-item-definition") {
    manifestText.value = newItemDefinitionYaml;
  } else if (route.query.prefill === "mob-definition") {
    await loadMobDefinitionYaml();
  } else if (route.query.prefill === "new-mob-definition") {
    manifestText.value = newMobDefinitionYaml;
  }
});

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(endpoint.value, {
      manifest: manifestText.value,
    });
    appliedKind.value = String(resp.data.kind || "").toLowerCase();
    lastOperation.value = String(resp.data.operation || "updated");
    appliedBatchSummary.value = appliedKind.value === "batch" ? resp.data.summary || null : null;

    if (appliedKind.value === "trigger") {
      appliedTrigger.value = resp.data.trigger || null;
      appliedItemTemplate.value = null;
      appliedItemDefinition.value = null;
      appliedMobDefinition.value = null;
    } else if (appliedKind.value === "world") {
      appliedTrigger.value = null;
      appliedItemTemplate.value = null;
      appliedItemDefinition.value = null;
      appliedMobDefinition.value = null;
    } else if (appliedKind.value === "itemtemplate") {
      appliedTrigger.value = null;
      appliedItemTemplate.value = resp.data.item_template || null;
      appliedItemDefinition.value = null;
      appliedMobDefinition.value = null;
    } else if (appliedKind.value === "itemdefinition") {
      appliedTrigger.value = null;
      appliedItemTemplate.value = null;
      appliedItemDefinition.value = resp.data.item_definition || null;
      appliedMobDefinition.value = null;
    } else if (appliedKind.value === "mobdefinition") {
      appliedTrigger.value = null;
      appliedItemTemplate.value = null;
      appliedItemDefinition.value = null;
      appliedMobDefinition.value = resp.data.mob_definition || null;
    } else if (appliedKind.value === "batch") {
      appliedTrigger.value = null;
      appliedItemTemplate.value = null;
      appliedItemDefinition.value = null;
      appliedMobDefinition.value = null;
    } else {
      appliedTrigger.value = null;
      appliedItemTemplate.value = null;
      appliedItemDefinition.value = null;
      appliedMobDefinition.value = null;
    }

    const freshWorld = await store.dispatch("builder/fetch_world", route.params.world_id);
    await Promise.all([
      store.dispatch("builder/worlds/config_fetch", {
        world_id: route.params.world_id,
      }),
      store.dispatch("builder/fetch_world_map", route.params.world_id),
    ]);
    if (freshWorld?.last_viewed_room) {
      store.commit("builder/room_set", freshWorld.last_viewed_room);
      store.commit("builder/zone_set", freshWorld.last_viewed_room.zone);
    }

    if (appliedKind.value === "batch" && appliedBatchSummary.value) {
      store.commit("ui/notification_set", `Applied ${appliedBatchSummary.value.documents} manifests.`);
    } else {
      const manifestLabel = appliedKind.value
        ? `${appliedKind.value} manifest`
        : "manifest";
      store.commit("ui/notification_set", `${capfirst(manifestLabel)} ${lastOperation.value}.`);
    }
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
  min-width: 0;
  width: 100%;

  .manifest-input {
    box-sizing: border-box;
    max-width: 100%;
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
