<template>
  <div id="edit-world-manifest">
    <template v-if="!hasApplyResult">
      <ManifestYamlEditor
        v-model="manifestText"
        :is-submitting="isSubmitting"
        copy-success-message="Manifest YAML copied."
        placeholder="Paste YAML manifest here..."
        save-label="APPLY MANIFEST"
        saving-label="APPLYING..."
        textarea-label="World YAML manifests"
        @save="submitManifest"
      >
        <template #header>
          <h2 class="definition-title">{{ world.name }}</h2>
          <div class="definition-meta color-text-60">
            Paste one or more YAML manifests. Each YAML document is applied in order.
            <a
              href="https://docs.writtenrealms.com/builders/yaml-manifests"
              target="_blank"
              rel="noopener noreferrer"
            >View supported kinds and examples.</a>
          </div>
        </template>
      </ManifestYamlEditor>
    </template>

    <template v-else>
      <h2 class="definition-title">{{ world.name }}</h2>
      <div class="definition-meta color-text-60">
        Paste one or more YAML manifests. Each YAML document is applied in order.
        <a
          href="https://docs.writtenrealms.com/builders/yaml-manifests"
          target="_blank"
          rel="noopener noreferrer"
        >View supported kinds and examples.</a>
      </div>

      <div class="manifest-result mt-6" aria-live="polite">
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
    </template>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref } from "vue";
import { type RouteLocationRaw, useRoute } from "vue-router";
import { useStore } from "vuex";
import ManifestYamlEditor from "@/components/builder/world/ManifestYamlEditor.vue";
import { capfirst } from "@/core/utils.ts";
import {
  builderRoomIndexRoute,
  builderZoneIndexRoute,
  roomRelativeIdFromRef,
  zoneRelativeId,
  zoneRelativeIdFromRef,
} from "@/core/builderRoutes";
import {
  fetchCraftMaterial,
  fetchCraftingProfile,
  fetchCraftingRecipe,
} from "@/services/crafting";

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
  craftmaterial: "craft_material",
  craftingprofile: "crafting_profile",
  craftingrecipe: "crafting_recipe",
  currency: "currency",
  faction: "faction",
  itembundle: "item_bundle",
  itemdefinition: "item_definition",
  merchantprofile: "merchant_profile",
  mobdefinition: "mob_definition",
  path: "path",
  quest: "quest",
  questarc: "quest_arc",
  room: "room",
  spawnplan: "spawn_plan",
  trigger: "trigger",
  zone: "zone",
};

const kindLabels: Record<string, string> = {
  ability: "Ability",
  abilities: "Ability",
  craftmaterial: "Craft material",
  craftingprofile: "Crafting profile",
  craftingrecipe: "Crafting recipe",
  currency: "Currency",
  faction: "Faction",
  itembundle: "Item bundle",
  itemdefinition: "Item",
  merchantprofile: "Merchant profile",
  mobdefinition: "Mob",
  path: "Path",
  quest: "Quest template",
  questarc: "Quest arc",
  room: "Room",
  spawnplan: "Spawn plan",
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
  const targetRoomRelativeId = roomRelativeIdFromRef(target.ref);
  const targetZoneRelativeId = zoneRelativeIdFromRef(target.ref);

  if (kind === "world") {
    return { name: "builder_world_config", params: { world_id: worldId } };
  }
  if (kind === "currency") {
    return { name: "builder_world_currency_list", params: { world_id: worldId } };
  }
  if (kind === "faction" && id) {
    return {
      name: "builder_world_faction_details",
      params: { world_id: worldId, faction_id: id },
    };
  }
  if (kind === "faction") {
    return { name: "builder_world_faction_list", params: { world_id: worldId } };
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
  if (kind === "trigger" && target.type === "room" && targetRoomRelativeId) {
    if (id) {
      return {
        name: "builder_room_trigger_details",
        params: { world_id: worldId, room_relative_id: targetRoomRelativeId, trigger_id: id },
      };
    }
    return {
      name: "builder_room_trigger_list",
      params: { world_id: worldId, room_relative_id: targetRoomRelativeId },
    };
  }
  if (kind === "trigger" && id && world.value?.builder_info?.builder_rank > 2) {
    return {
      name: "builder_world_trigger_details",
      params: { world_id: worldId, trigger_id: id },
    };
  }
  if (kind === "trigger" && target.type === "zone" && (targetZoneRelativeId || targetId)) {
    return builderZoneIndexRoute(worldId as string, {
      id: targetId,
      relative_id: targetZoneRelativeId,
    });
  }
  if (kind === "trigger" && target.type === "world") {
    return { name: "builder_world_config", params: { world_id: worldId } };
  }
  if (kind === "zone" && (id || zoneRelativeId(payload))) {
    return builderZoneIndexRoute(worldId as string, payload);
  }
  if (kind === "room" && id) {
    const relativeId = roomRelativeIdFromRef(payload?.ref);
    if (relativeId) {
      return {
        name: "builder_room_index",
        params: { world_id: worldId, room_relative_id: relativeId },
      };
    }
    return builderRoomIndexRoute(worldId as string, payload);
  }
  if (kind === "path" && id && payload?.zone?.id) {
    const relativeId = zoneRelativeId(payload.zone);
    if (!relativeId) return builderZoneIndexRoute(worldId as string, payload.zone);
    return {
      name: "builder_zone_path_details",
      params: { world_id: worldId, zone_relative_id: relativeId, path_id: id },
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
  if (kind === "craftmaterial" && id) {
    return {
      name: "builder_world_craft_material_details",
      params: { world_id: worldId, craft_material_id: id },
    };
  }
  if (kind === "craftmaterial") {
    return { name: "builder_world_craft_material_list", params: { world_id: worldId } };
  }
  if (kind === "craftingrecipe" && id) {
    return {
      name: "builder_world_crafting_recipe_details",
      params: { world_id: worldId, crafting_recipe_id: id },
    };
  }
  if (kind === "craftingrecipe") {
    return { name: "builder_world_crafting_recipe_list", params: { world_id: worldId } };
  }
  if (kind === "craftingprofile" && id) {
    return {
      name: "builder_world_crafting_profile_details",
      params: { world_id: worldId, crafting_profile_id: id },
    };
  }
  if (kind === "craftingprofile") {
    return { name: "builder_world_crafting_profile_list", params: { world_id: worldId } };
  }
  if (kind === "quest" && id) {
    return {
      name: "builder_world_quest_template_details",
      params: { world_id: worldId, quest_template_id: id },
    };
  }
  if (kind === "spawnplan" && id && payload?.zone?.id) {
    return { name: "builder_world_edit", params: { world_id: worldId } };
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

const defaultCurrencyCode = () => (
  store.state.builder.world?.default_currency
  || store.state.builder.world?.currencies?.find((currency: any) => currency.is_default)?.code
  || store.state.builder.world?.currencies?.[0]?.code
  || "currency"
);

const newItemDefinitionYaml = () => `kind: itemdefinition
metadata:
  slug: new-item
  name: a new item
spec:
  description: ''
  room_description: A new item lies here.
  notes: ''
  keywords: item
  type: inert
  quality: normal
  is_persistent: false
  is_pickable: true
  cost: 0
  currency: ${defaultCurrencyCode()}
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

const newFactionYaml = `kind: faction
metadata:
  code: new_faction
  name: New Faction
spec:
  type: reputation
  description: ''
  notes: ''
  ranks:
    - standing: -1000
      name: Hostile
    - standing: 0
      name: Neutral
    - standing: 1000
      name: Friendly
`;

const newMobDefinitionYaml = () => `kind: mobdefinition
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
  rewards:
    currencies: {}
  aggression: normal
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

const newMerchantProfileYaml = () => `kind: merchantprofile
metadata:
  slug: new-merchant-profile
  name: New Merchant Profile
spec:
  notes: ''
  settlement_currency: ${defaultCurrencyCode()}
  pricing:
    sell_markup: 1
    buy_multiplier: 0.4
  restock:
    interval_seconds:
  funds:
    mode: unlimited
    purchase_budget: 0
  buyback:
    enabled: false
    max_items: 0
    expires: on_restock
  stock: []
`;

const newCraftMaterialYaml = `apiVersion: v1alpha1
kind: craftmaterial
metadata:
  slug: new-material
  name: New Material
spec:
  description: A reusable material recovered through salvage.
  order: 10
`;

const newCraftingRecipeYaml = `apiVersion: v1alpha1
kind: craftingrecipe
metadata:
  slug: new-recipe
spec:
  group: armor
  order: 10
  cost: 1
  currency: ${defaultCurrencyCode()}
  output:
    item_definition: itemdefinition.new-item
  inputs:
    - material: craftmaterial.new-material
      quantity: 1
  conditions: {}
  failure_message: You cannot craft that here.
`;

const newCraftingProfileYaml = `apiVersion: v1alpha1
kind: craftingprofile
metadata:
  slug: new-workshop
  name: New Workshop
spec:
  keywords: workshop forge
  recipes: []
`;

const newAbilityYaml = `kind: ability
metadata:
  world: world.${route.params.world_id}
  slug: new-ability
  name: New Ability
spec:
  command:
    verbs: [newability]
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

const newSpawnPlanYaml = () => {
  const zone = store.state.builder.zone;
  const zoneRef = zone?.manifest_ref || (zone?.relative_id ? `zone@${zone.relative_id}` : "zone@ZONE_RELATIVE_ID");
  const zoneName = zone?.name || "Zone";
  return `kind: spawnplan
metadata:
  slug: ${slugifyName(`${zoneName} Spawn Plan`)}
  name: ${zoneName} Spawn Plan
spec:
  zone: ${zoneRef}
  respawn:
    mode: fixed
    seconds: 300
  entries: []
`;
};

const newTriggerYaml = (roomRefOverride?: string) => {
  const roomRef = (
    roomRefOverride
    || store.state.builder.room?.manifest_ref
    || "room@ROOM_RELATIVE_ID"
  );
  return `kind: trigger
metadata:
  world: world.${route.params.world_id}
  name: New Room Trigger
spec:
  scope: room
  kind: command
  target: ${roomRef}
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

const slugifyName = (value: string): string => {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "new-spawn-plan";
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

const loadFactionYaml = async () => {
  clearApplyResult();
  const rawFactionId = route.query.faction_id;
  const factionId = Array.isArray(rawFactionId)
    ? rawFactionId[0]
    : rawFactionId;
  if (!factionId) {
    manifestText.value = "";
    return;
  }
  try {
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/factions/${factionId}/`
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
  manifestText.value = newMobDefinitionYaml();
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

const loadCraftMaterialYaml = async () => {
  clearApplyResult();
  const rawMaterialId = route.query.craft_material_id;
  const materialId = Array.isArray(rawMaterialId) ? rawMaterialId[0] : rawMaterialId;
  if (!materialId) {
    manifestText.value = "";
    return;
  }
  try {
    const material = await fetchCraftMaterial(String(route.params.world_id), materialId);
    manifestText.value = material.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

const loadCraftingRecipeYaml = async () => {
  clearApplyResult();
  const rawRecipeId = route.query.crafting_recipe_id;
  const recipeId = Array.isArray(rawRecipeId) ? rawRecipeId[0] : rawRecipeId;
  if (!recipeId) {
    manifestText.value = "";
    return;
  }
  try {
    const recipe = await fetchCraftingRecipe(String(route.params.world_id), recipeId);
    manifestText.value = recipe.yaml || "";
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  }
};

const loadCraftingProfileYaml = async () => {
  clearApplyResult();
  const rawProfileId = route.query.crafting_profile_id;
  const profileId = Array.isArray(rawProfileId) ? rawProfileId[0] : rawProfileId;
  if (!profileId) {
    manifestText.value = "";
    return;
  }
  try {
    const profile = await fetchCraftingProfile(String(route.params.world_id), profileId);
    manifestText.value = profile.yaml || "";
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
  const rawRoomRef = route.query.room_ref;
  const roomRef = Array.isArray(rawRoomRef)
    ? rawRoomRef[0]
    : rawRoomRef;
  const relativeId = roomRelativeIdFromRef(roomRef);
  if (!roomRef || !relativeId) {
    manifestText.value = newTriggerYaml();
    return;
  }
  try {
    let targetRoom = store.state.builder.room;
    if (targetRoom?.manifest_ref !== roomRef) {
      const roomResp = await axios.get(
        `/builder/worlds/${route.params.world_id}/rooms/by-relative-id/${relativeId}/`
      );
      targetRoom = roomResp.data;
    }
    const resp = await axios.get(
      `/builder/worlds/${route.params.world_id}/rooms/${targetRoom.id}/triggers/`
    );
    manifestText.value = resp.data?.new_trigger_template?.yaml || newTriggerYaml(roomRef);
  } catch (error: any) {
    manifestText.value = newTriggerYaml(roomRef);
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
    manifestText.value = newItemDefinitionYaml();
  } else if (route.query.prefill === "item-bundle") {
    await loadItemBundleYaml();
  } else if (route.query.prefill === "new-item-bundle") {
    clearApplyResult();
    manifestText.value = newItemBundleYaml;
  } else if (route.query.prefill === "faction") {
    await loadFactionYaml();
  } else if (route.query.prefill === "new-faction") {
    clearApplyResult();
    manifestText.value = newFactionYaml;
  } else if (route.query.prefill === "mob-definition") {
    await loadMobDefinitionYaml();
  } else if (route.query.prefill === "suggested-mob-definition") {
    loadSuggestedMobDefinitionYaml();
  } else if (route.query.prefill === "new-mob-definition") {
    clearApplyResult();
    manifestText.value = newMobDefinitionYaml();
  } else if (route.query.prefill === "new-spawn-plan") {
    clearApplyResult();
    manifestText.value = newSpawnPlanYaml();
  } else if (route.query.prefill === "merchant-profile") {
    await loadMerchantProfileYaml();
  } else if (route.query.prefill === "new-merchant-profile") {
    clearApplyResult();
    manifestText.value = newMerchantProfileYaml();
  } else if (route.query.prefill === "craft-material") {
    await loadCraftMaterialYaml();
  } else if (route.query.prefill === "new-craft-material") {
    clearApplyResult();
    manifestText.value = newCraftMaterialYaml;
  } else if (route.query.prefill === "crafting-recipe") {
    await loadCraftingRecipeYaml();
  } else if (route.query.prefill === "new-crafting-recipe") {
    clearApplyResult();
    manifestText.value = newCraftingRecipeYaml;
  } else if (route.query.prefill === "crafting-profile") {
    await loadCraftingProfileYaml();
  } else if (route.query.prefill === "new-crafting-profile") {
    clearApplyResult();
    manifestText.value = newCraftingProfileYaml;
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
  box-sizing: border-box;
  min-width: 0;
  width: 100%;

  .definition-title {
    margin-bottom: 0.35rem;
  }

  .definition-meta {
    line-height: 1.4;
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
