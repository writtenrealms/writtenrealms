<template>
  <div id="edit-world-manifest">
    <h2>{{ world.name.toUpperCase() }} EDIT WORLD</h2>
    <div class="color-text-60 mb-6">
      Paste one or more YAML manifests. Each YAML document is applied in order. Supported kinds: world, currency, zone, room, itemtemplate, itemdefinition, itembundle, merchantprofile, mobtemplate, mobdefinition, ability, abilities, quest, questarc, and trigger.
    </div>

    <template v-if="!hasApplyResult">
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
    </template>

    <div v-else class="manifest-result mt-6">
      <h3>Manifest Applied</h3>
      <div class="manifest-result-summary color-text-60">{{ appliedSummaryText }}</div>

      <ul v-if="appliedEntities.length" class="manifest-entity-list">
        <li v-for="entity in appliedEntities" :key="entity.key" class="manifest-entity-row">
          <span class="manifest-entity-operation">{{ capfirst(entity.operation) }}</span>
          <span class="manifest-entity-kind">{{ entity.kindLabel }}</span>
          <router-link v-if="entity.to" :to="entity.to" class="manifest-entity-link">
            {{ entity.name }}
          </router-link>
          <span v-else class="manifest-entity-name">{{ entity.name }}</span>
        </li>
      </ul>
      <div v-else class="color-text-60">No linkable entities were returned.</div>

      <div class="manifest-actions mt-4">
        <button class="btn-small" @click="startAnotherManifest">APPLY ANOTHER MANIFEST</button>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref } from "vue";
import { type RouteLocationRaw, useRoute } from "vue-router";
import { useStore } from "vuex";
import { capfirst } from "@/core/utils.ts";

const store = useStore();
const route = useRoute();

const world = computed(() => store.state.builder.world);
const manifestText = ref("");
const isSubmitting = ref(false);
const appliedKind = ref<string>("");
const appliedBatchSummary = ref<any | null>(null);
const appliedEntities = ref<AppliedEntity[]>([]);
const lastOperation = ref<string>("");

const endpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);
const hasApplyResult = computed(() => Boolean(appliedKind.value && lastOperation.value));
const batchSummaryText = computed(() => {
  const kinds = appliedBatchSummary.value?.kinds || {};
  const labels = Object.entries(kinds).map(([kind, count]) => {
    const suffix = Number(count) === 1 ? "" : "s";
    return `${count} ${kind}${suffix}`;
  });
  return labels.join(", ");
});
const appliedSummaryText = computed(() => {
  if (appliedKind.value === "batch" && appliedBatchSummary.value) {
    const suffix = batchSummaryText.value ? `: ${batchSummaryText.value}` : "";
    return `Applied ${appliedBatchSummary.value.documents} documents${suffix}.`;
  }
  if (appliedEntities.value.length === 1) {
    const entity = appliedEntities.value[0];
    return `${capfirst(entity.operation)} ${entity.kindLabel.toLowerCase()} "${entity.name}".`;
  }
  const manifestLabel = appliedKind.value ? `${kindLabel(appliedKind.value)} manifest` : "manifest";
  return `${capfirst(lastOperation.value)} ${manifestLabel}.`;
});

type AppliedEntity = {
  key: string;
  kind: string;
  kindLabel: string;
  operation: string;
  name: string;
  to?: RouteLocationRaw;
};

const payloadKeyByKind: Record<string, string> = {
  ability: "ability",
  currency: "currency",
  itembundle: "item_bundle",
  itemdefinition: "item_definition",
  itemtemplate: "item_template",
  merchantprofile: "merchant_profile",
  mobdefinition: "mob_definition",
  mobtemplate: "mob_template",
  quest: "quest",
  questarc: "quest_arc",
  room: "room",
  trigger: "trigger",
  zone: "zone",
};

const kindLabels: Record<string, string> = {
  ability: "Ability",
  abilities: "Ability",
  currency: "Currency",
  itembundle: "Item bundle",
  itemdefinition: "Item",
  itemtemplate: "Item template",
  merchantprofile: "Merchant profile",
  mobdefinition: "Mob",
  mobtemplate: "Mob template",
  quest: "Quest template",
  questarc: "Quest arc",
  room: "Room",
  trigger: "Trigger",
  world: "World config",
  zone: "Zone",
};

const kindLabel = (kind: string): string => {
  return kindLabels[kind] || kind.replace(/_/g, " ");
};

const clearApplyResult = () => {
  appliedKind.value = "";
  appliedBatchSummary.value = null;
  appliedEntities.value = [];
  lastOperation.value = "";
};

const startAnotherManifest = () => {
  clearApplyResult();
  manifestText.value = "";
};

const parseEntityIdFromKey = (key: any): string | null => {
  const match = String(key || "").match(/(?:^|\.)(\d+)$/);
  return match ? match[1] : null;
};

const routeForEntity = (
  kind: string,
  payload: any,
  operation: string
): RouteLocationRaw | undefined => {
  if (operation === "deleted") return undefined;

  const worldId = route.params.world_id;
  const id = payload?.id;
  const target = payload?.target || {};
  const targetId = parseEntityIdFromKey(target.key);

  if (kind === "world") {
    return { name: "builder_world_config", params: { world_id: worldId } };
  }
  if (kind === "currency") {
    return { name: "builder_world_currency_list", params: { world_id: worldId } };
  }
  if (kind === "ability" && id) {
    return {
      name: "builder_world_ability_details",
      params: { world_id: worldId, ability_id: id },
    };
  }
  if (kind === "ability") {
    return { name: "builder_world_ability_list", params: { world_id: worldId } };
  }
  if (kind === "trigger" && target.type === "room" && targetId) {
    if (id) {
      return {
        name: "builder_room_trigger_details",
        params: { world_id: worldId, room_id: targetId, trigger_id: id },
      };
    }
    return {
      name: "builder_room_trigger_list",
      params: { world_id: worldId, room_id: targetId },
    };
  }
  if (kind === "trigger" && id && world.value?.builder_info?.builder_rank > 2) {
    return {
      name: "builder_world_trigger_details",
      params: { world_id: worldId, trigger_id: id },
    };
  }
  if (kind === "trigger" && target.type === "zone" && targetId) {
    return {
      name: "builder_zone_index",
      params: { world_id: worldId, zone_id: targetId },
    };
  }
  if (kind === "trigger" && target.type === "world") {
    return { name: "builder_world_config", params: { world_id: worldId } };
  }
  if (kind === "zone" && id) {
    return { name: "builder_zone_index", params: { world_id: worldId, zone_id: id } };
  }
  if (kind === "room" && id) {
    return { name: "builder_room_index", params: { world_id: worldId, room_id: id } };
  }
  if (kind === "itemtemplate" && id) {
    return {
      name: "builder_item_template_details",
      params: { world_id: worldId, item_template_id: id },
    };
  }
  if (kind === "itemdefinition" && id) {
    return {
      name: "builder_item_definition_details",
      params: { world_id: worldId, item_definition_id: id },
    };
  }
  if (kind === "itembundle" && id) {
    return {
      name: "builder_item_bundle_details",
      params: { world_id: worldId, item_bundle_id: id },
    };
  }
  if (kind === "mobtemplate" && id) {
    return {
      name: "builder_mob_template_details",
      params: { world_id: worldId, mob_template_id: id },
    };
  }
  if (kind === "mobdefinition" && id) {
    return {
      name: "builder_mob_definition_details",
      params: { world_id: worldId, mob_definition_id: id },
    };
  }
  if (kind === "merchantprofile" && id) {
    return {
      name: "builder_merchant_profile_details",
      params: { world_id: worldId, merchant_profile_id: id },
    };
  }
  if (kind === "quest" && id) {
    return {
      name: "builder_world_quest_template_details",
      params: { world_id: worldId, quest_template_id: id },
    };
  }

  return undefined;
};

const entityName = (kind: string, payload: any): string => {
  if (kind === "world") return world.value.name;
  return String(
    payload?.name ||
      payload?.slug ||
      payload?.ref ||
      payload?.code ||
      payload?.key ||
      kindLabel(kind)
  );
};

const entityKey = (kind: string, operation: string, payload: any, index: number): string => {
  const id = payload?.id || payload?.key || payload?.slug || payload?.code || payload?.ref || index;
  return `${kind}:${operation}:${id}:${index}`;
};

const appliedEntityFromPayload = (
  kind: string,
  operation: string,
  payload: any,
  index: number
): AppliedEntity => {
  return {
    key: entityKey(kind, operation, payload, index),
    kind,
    kindLabel: kindLabel(kind),
    operation,
    name: entityName(kind, payload),
    to: routeForEntity(kind, payload, operation),
  };
};

const entitiesForResult = (result: any, startIndex = 0): AppliedEntity[] => {
  const kind = String(result?.kind || "").toLowerCase();
  const operation = String(result?.operation || "updated");

  if (kind === "abilities") {
    return (result?.abilities || []).map((ability, index) =>
      appliedEntityFromPayload("ability", operation, ability, startIndex + index)
    );
  }

  if (kind === "world") {
    return [appliedEntityFromPayload(kind, operation, {}, startIndex)];
  }

  const payloadKey = payloadKeyByKind[kind];
  const payload = payloadKey ? result?.[payloadKey] : null;
  if (!payload) return [];
  return [appliedEntityFromPayload(kind, operation, payload, startIndex)];
};

const setAppliedResult = (data: any) => {
  appliedKind.value = String(data.kind || "").toLowerCase();
  lastOperation.value = String(data.operation || "updated");
  appliedBatchSummary.value = appliedKind.value === "batch" ? data.summary || null : null;

  if (appliedKind.value === "batch") {
    appliedEntities.value = (data.results || []).flatMap((result, index) =>
      entitiesForResult(result, index)
    );
  } else {
    appliedEntities.value = entitiesForResult(data);
  }
};

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
  clearApplyResult();
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
  description: ''
  ground_description: A new item lies here.
  notes: ''
  keywords: item
  type: inert
  quality: normal
  is_persistent: false
  is_pickable: true
  cost: 0
  food_value: 0
  equipment_type:
  armor_class: light
  weapon_damage: 0
  health_max: 0
  health_regen: 0
  energy_max: 0
  energy_regen: 0
  stamina_max: 0
  stamina_regen: 0
  attack_power: 0
  ability_power: 0
  resilience: 0
  dodge: 0
  crit: 0
  attributes: {}
  randomization:
    attributes: []
`;

const newItemBundleYaml = `kind: itembundle
metadata:
  slug: new-item-bundle
  name: New Item Bundle
spec:
  notes: ''
  entries: []
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

const newMerchantProfileYaml = `kind: merchantprofile
metadata:
  slug: new-merchant-profile
  name: New Merchant Profile
spec:
  notes: ''
  pricing:
    sell_markup: 1
    buy_multiplier: 0.4
  restock:
    interval_seconds:
  funds:
    mode: unlimited
    currency: ''
    purchase_budget: 0
  buyback:
    enabled: false
    max_items: 0
    expires: on_restock
  stock: []
`;

const newAbilityYaml = `kind: ability
metadata:
  world: world.${route.params.world_id}
  slug: new-ability
  name: New Ability
spec:
  command:
    verbs: [newability]
  action_type: primary
  target:
    type: hostile
    default: current_target
  availability:
    classes: []
    min_level: 1
  requirements: {}
  cost: {}
  cast_time:
    rounds: 0
  cooldown:
    rounds: 0
  components:
    - type: damage
      profile: basic_ability
      text:
        label: New Ability
  is_active: true
`;

const newTriggerYaml = (roomIdOverride?: string) => {
  const roomId = roomIdOverride || store.state.builder.room?.id || "ROOM_ID";
  return `kind: trigger
metadata:
  world: world.${route.params.world_id}
  name: New Room Trigger
spec:
  scope: room
  kind: command
  target:
    type: room
    key: room.${roomId}
  match: pull lever
  script: |
    /cmd room -- /echo -- The lever clicks.
    /cmd room -- /echo -- Something happens.
  conditions: ""
  show_details_on_failure: false
  failure_message: ""
  display_action_in_room: true
  gate_delay: 10
  order: 0
  is_active: true
`;
};

const loadItemDefinitionYaml = async () => {
  clearApplyResult();
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

const loadItemBundleYaml = async () => {
  clearApplyResult();
  const rawItemBundleId = route.query.item_bundle_id;
  const itemBundleId = Array.isArray(rawItemBundleId)
    ? rawItemBundleId[0]
    : rawItemBundleId;
  if (!itemBundleId) {
    manifestText.value = "";
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/itembundles/${itemBundleId}/`
    );
    manifestText.value = resp.data?.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

const loadMobDefinitionYaml = async () => {
  clearApplyResult();
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

const loadSuggestedMobDefinitionYaml = () => {
  clearApplyResult();
  const storageKey = `wr:mob-definition-suggestion:${route.params.world_id}`;
  const yaml = window.sessionStorage.getItem(storageKey);
  if (yaml) {
    manifestText.value = yaml;
    window.sessionStorage.removeItem(storageKey);
    return;
  }
  manifestText.value = newMobDefinitionYaml;
};

const loadMerchantProfileYaml = async () => {
  clearApplyResult();
  const rawMerchantProfileId = route.query.merchant_profile_id;
  const merchantProfileId = Array.isArray(rawMerchantProfileId)
    ? rawMerchantProfileId[0]
    : rawMerchantProfileId;
  if (!merchantProfileId) {
    manifestText.value = "";
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/merchantprofiles/${merchantProfileId}/`
    );
    manifestText.value = resp.data?.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

const loadAbilityYaml = async () => {
  clearApplyResult();
  const rawAbilityId = route.query.ability_id;
  const abilityId = Array.isArray(rawAbilityId)
    ? rawAbilityId[0]
    : rawAbilityId;
  if (!abilityId) {
    manifestText.value = "";
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/abilities/${abilityId}/`
    );
    manifestText.value = resp.data?.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

const loadTriggerYaml = async () => {
  clearApplyResult();
  const rawTriggerId = route.query.trigger_id;
  const triggerId = Array.isArray(rawTriggerId)
    ? rawTriggerId[0]
    : rawTriggerId;
  if (!triggerId) {
    manifestText.value = "";
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/triggers/${triggerId}/`
    );
    manifestText.value = resp.data?.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

const loadNewRoomTriggerYaml = async () => {
  clearApplyResult();
  const rawRoomId = route.query.room_id;
  const roomId = Array.isArray(rawRoomId)
    ? rawRoomId[0]
    : rawRoomId;
  if (!roomId) {
    manifestText.value = newTriggerYaml();
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/rooms/${roomId}/triggers/`
    );
    manifestText.value = resp.data?.new_trigger_template?.yaml || newTriggerYaml(roomId);
  } catch (error: any) {
    manifestText.value = newTriggerYaml(roomId);
    store.commit("ui/notification_set_error", extractError(error));
  }
};

onMounted(async () => {
  if (route.query.prefill === "world-config") {
    await loadWorldConfigYaml();
  } else if (route.query.prefill === "item-definition") {
    await loadItemDefinitionYaml();
  } else if (route.query.prefill === "new-item-definition") {
    clearApplyResult();
    manifestText.value = newItemDefinitionYaml;
  } else if (route.query.prefill === "item-bundle") {
    await loadItemBundleYaml();
  } else if (route.query.prefill === "new-item-bundle") {
    clearApplyResult();
    manifestText.value = newItemBundleYaml;
  } else if (route.query.prefill === "mob-definition") {
    await loadMobDefinitionYaml();
  } else if (route.query.prefill === "suggested-mob-definition") {
    loadSuggestedMobDefinitionYaml();
  } else if (route.query.prefill === "new-mob-definition") {
    clearApplyResult();
    manifestText.value = newMobDefinitionYaml;
  } else if (route.query.prefill === "merchant-profile") {
    await loadMerchantProfileYaml();
  } else if (route.query.prefill === "new-merchant-profile") {
    clearApplyResult();
    manifestText.value = newMerchantProfileYaml;
  } else if (route.query.prefill === "ability") {
    await loadAbilityYaml();
  } else if (route.query.prefill === "new-ability") {
    clearApplyResult();
    manifestText.value = newAbilityYaml;
  } else if (route.query.prefill === "trigger") {
    await loadTriggerYaml();
  } else if (route.query.prefill === "new-room-trigger") {
    await loadNewRoomTriggerYaml();
  } else if (route.query.prefill === "new-trigger") {
    clearApplyResult();
    manifestText.value = newTriggerYaml();
  }
});

const submitManifest = async () => {
  isSubmitting.value = true;
  clearApplyResult();
  try {
    const resp = await axios.post(endpoint.value, {
      manifest: manifestText.value,
    });
    setAppliedResult(resp.data);

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

  .manifest-result {
    border-top: 1px solid $color-background-light-border;
    max-width: 760px;
    padding-top: 1.25rem;

    h3 {
      margin-bottom: 0.5rem;
    }
  }

  .manifest-result-summary {
    margin-bottom: 1rem;
  }

  .manifest-entity-list {
    display: grid;
    gap: 0.6rem;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .manifest-entity-row {
    align-items: baseline;
    display: grid;
    gap: 0.75rem;
    grid-template-columns: 5.5rem 8rem minmax(0, 1fr);
  }

  .manifest-entity-operation,
  .manifest-entity-kind {
    color: $color-text-hex-60;
  }

  .manifest-entity-link,
  .manifest-entity-name {
    min-width: 0;
    overflow-wrap: anywhere;
  }
}
</style>
