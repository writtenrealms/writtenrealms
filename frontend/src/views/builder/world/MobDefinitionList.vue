<template>
  <div v-if="store.state.builder.world.instance_of.id">
    <h2 class="mb-4">MOBS</h2>
    <p>The mobs of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_mob_definition_list', params: { world_id: store.state.builder.world.instance_of.id } }"
      >
        {{ store.state.builder.world.instance_of.name }} Mobs
      </router-link>
    </p>
  </div>

  <template v-else>
    <Teleport to="body">
      <div
        v-if="showAddForm"
        class="mob-suggestion-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mob-suggestion-title"
        @click.self="cancelAdd"
      >
        <form class="mob-suggestion-form" @submit.prevent="createSuggestedMob">
          <h3 id="mob-suggestion-title">ADD MOB</h3>
          <div class="mob-suggestion-grid">
            <div class="form-group">
              <label for="mob-suggestion-name">Mob name</label>
              <input
                id="mob-suggestion-name"
                v-model="addForm.name"
                type="text"
                required
                @input="onNameInput"
              />
            </div>
            <div class="form-group">
              <label for="mob-suggestion-slug">Mob slug</label>
              <input
                id="mob-suggestion-slug"
                v-model="addForm.slug"
                type="text"
                required
                @input="slugWasEdited = true"
              />
            </div>
            <div class="form-group">
              <label for="mob-suggestion-type">Mob type</label>
              <select id="mob-suggestion-type" v-model="addForm.type">
                <option v-for="option in mobTypeOptions" :key="option.key" :value="option.key">
                  {{ option.name }}
                </option>
              </select>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-faction">Faction</label>
              <select id="mob-suggestion-faction" v-model="addForm.faction">
                <option value="">None</option>
                <option v-for="option in coreFactionOptions" :key="option.key" :value="option.key">
                  {{ option.name }}
                </option>
              </select>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-level">Mob level</label>
              <input
                id="mob-suggestion-level"
                v-model.number="addForm.level"
                type="number"
                min="1"
                required
              />
            </div>
            <div class="form-group">
              <label for="mob-suggestion-health-max">Health max</label>
              <input
                id="mob-suggestion-health-max"
                v-model.number="addForm.healthMax"
                type="number"
                min="1"
                step="1"
                :placeholder="directStatPlaceholder('health_max')"
              />
              <div v-if="directStatDefaultLabel('health_max')" class="mob-suggestion-default">
                {{ directStatDefaultLabel('health_max') }}
              </div>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-weapon-damage">Weapon damage</label>
              <input
                id="mob-suggestion-weapon-damage"
                v-model.number="addForm.weaponDamage"
                type="number"
                min="0"
                step="0.01"
                :placeholder="directStatPlaceholder('weapon_damage')"
              />
              <div v-if="directStatDefaultLabel('weapon_damage')" class="mob-suggestion-default">
                {{ directStatDefaultLabel('weapon_damage') }}
              </div>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-attack-power">Attack power</label>
              <input
                id="mob-suggestion-attack-power"
                v-model.number="addForm.attackPower"
                type="number"
                min="0"
                step="1"
                :placeholder="directStatPlaceholder('attack_power')"
              />
              <div v-if="directStatDefaultLabel('attack_power')" class="mob-suggestion-default">
                {{ directStatDefaultLabel('attack_power') }}
              </div>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-crit">Crit %</label>
              <input
                id="mob-suggestion-crit"
                v-model.number="addForm.critPercent"
                type="number"
                min="0"
                max="100"
                step="0.01"
                :placeholder="ratingPlaceholder('crit')"
              />
              <div v-if="ratingDefaultLabel('crit')" class="mob-suggestion-default">
                {{ ratingDefaultLabel('crit') }}
              </div>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-resilience">Resilience %</label>
              <input
                id="mob-suggestion-resilience"
                v-model.number="addForm.resiliencePercent"
                type="number"
                min="0"
                max="100"
                step="0.01"
                :placeholder="ratingPlaceholder('resilience')"
              />
              <div v-if="ratingDefaultLabel('resilience')" class="mob-suggestion-default">
                {{ ratingDefaultLabel('resilience') }}
              </div>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-armor">Armor %</label>
              <input
                id="mob-suggestion-armor"
                v-model.number="addForm.armorPercent"
                type="number"
                min="0"
                max="100"
                step="0.01"
                :placeholder="ratingPlaceholder('armor')"
              />
              <div v-if="ratingDefaultLabel('armor')" class="mob-suggestion-default">
                {{ ratingDefaultLabel('armor') }}
              </div>
            </div>
            <div class="form-group">
              <label for="mob-suggestion-dodge">Dodge %</label>
              <input
                id="mob-suggestion-dodge"
                v-model.number="addForm.dodgePercent"
                type="number"
                min="0"
                max="100"
                step="0.01"
                :placeholder="ratingPlaceholder('dodge')"
              />
              <div v-if="ratingDefaultLabel('dodge')" class="mob-suggestion-default">
                {{ ratingDefaultLabel('dodge') }}
              </div>
            </div>
          </div>
          <div v-if="addError" class="mob-suggestion-error">{{ addError }}</div>
          <div class="mob-suggestion-actions">
            <button class="btn-small" type="submit" :disabled="isSuggesting">
              GENERATE YAML
            </button>
            <button class="btn-small button-gray" type="button" :disabled="isSuggesting" @click="cancelAdd">
              CANCEL
            </button>
          </div>
        </form>
      </div>
    </Teleport>

    <ElementList
      title="Mobs"
      :schema="listSchema"
      :filters="listFilters"
      :endpoint="endpoint"
      :resolve_route="resolveRoute"
      filter-display="dropdown"
      mobile-filter-row
      table-variant="data"
      default-sort="-modified_ts"
      @add="onClickAdd"
    />
  </template>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatPercent, formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const route = useRoute();
const router = useRouter();

const endpoint = `/builder/worlds/${route.params.world_id}/mobdefinitions/`;
const suggestionEndpoint = `/builder/worlds/${route.params.world_id}/balance/mob-suggestions/`;

const mobTypeOptions = [
  { key: "humanoid", name: "Humanoid" },
  { key: "aberration", name: "Aberration" },
  { key: "beast", name: "Beast" },
  { key: "celestial", name: "Celestial" },
  { key: "construct", name: "Construct" },
  { key: "dragon", name: "Dragon" },
  { key: "elemental", name: "Elemental" },
  { key: "fey", name: "Fey" },
  { key: "fiend", name: "Fiend" },
  { key: "giant", name: "Giant" },
  { key: "monstrosity", name: "Monstrosity" },
  { key: "ooze", name: "Ooze" },
  { key: "plant", name: "Plant" },
  { key: "undead", name: "Undead" },
];

const showAddForm = ref(false);
const isSuggesting = ref(false);
const addError = ref("");
const slugWasEdited = ref(false);
const defaultRatingPercents = ref<Record<string, number>>({});
const defaultDirectStats = ref<Record<string, number>>({});
const addForm = ref({
  name: "a new mob",
  slug: "new-mob",
  type: "humanoid",
  level: 1,
  healthMax: null as number | null,
  weaponDamage: null as number | null,
  attackPower: null as number | null,
  faction: "",
  critPercent: null as number | null,
  resiliencePercent: null as number | null,
  armorPercent: null as number | null,
  dodgePercent: null as number | null,
});

let defaultPreviewTimeout: ReturnType<typeof setTimeout> | null = null;
let defaultPreviewRequestId = 0;

const ratingPreviewKeys = {
  armor: "same_level_armor_mitigation",
  crit: "same_level_crit_chance",
  dodge: "same_level_dodge_chance",
  resilience: "same_level_resilience_mitigation",
};

const directStatKeys = [
  "health_max",
  "weapon_damage",
  "attack_power",
];

const resolveRoute = element => {
  return {
    name: "builder_mob_definition_details",
    params: {
      world_id: store.state.builder.world.id,
      mob_definition_id: element.id,
    },
  };
};

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate,
  },
];

const coreFactionOptions = computed(() => {
  const factions = store.state.builder.world?.factions;
  if (!Array.isArray(factions)) return [];

  return factions
    .filter(faction => faction.type === "core" || faction.is_core)
    .map(faction => ({ key: faction.code, name: faction.name }))
    .sort((left, right) => left.name.localeCompare(right.name));
});

const listFilters = computed(() => [
  {
    label: "Type",
    attr: "type",
    filter_options: mobTypeOptions,
  },
  {
    label: "Faction",
    attr: "faction",
    filter_options: coreFactionOptions.value,
  },
]);

const suggestionStorageKey = () => {
  return `wr:mob-definition-suggestion:${route.params.world_id}`;
};

const slugifyMobName = (value: string): string => {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/^(a|an|the)\s+/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "new-mob";
};

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return error?.message || "Could not generate mob YAML.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not generate mob YAML.";
  if (typeof data === "object") {
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not generate mob YAML.";
};

const onClickAdd = () => {
  showAddForm.value = true;
  addError.value = "";
};

const cancelAdd = () => {
  showAddForm.value = false;
  addError.value = "";
};

const onNameInput = () => {
  if (!slugWasEdited.value) {
    addForm.value.slug = slugifyMobName(addForm.value.name);
  }
};

const createSuggestedMob = async () => {
  addError.value = "";
  isSuggesting.value = true;
  try {
    const payload: Record<string, any> = {
      name: addForm.value.name,
      slug: addForm.value.slug,
      type: addForm.value.type,
      level: Number(addForm.value.level || 1),
    };
    addNumericField(payload, "health_max", addForm.value.healthMax);
    addNumericField(payload, "weapon_damage", addForm.value.weaponDamage);
    addNumericField(payload, "attack_power", addForm.value.attackPower);
    if (addForm.value.faction) {
      payload.faction = addForm.value.faction;
    }
    addNumericField(payload, "crit_percent", addForm.value.critPercent);
    addNumericField(payload, "resilience_percent", addForm.value.resiliencePercent);
    addNumericField(payload, "armor_percent", addForm.value.armorPercent);
    addNumericField(payload, "dodge_percent", addForm.value.dodgePercent);

    const resp = await axios.post(suggestionEndpoint, payload);
    const yaml = resp.data?.yaml || "";
    if (!yaml) throw new Error("Suggestion response did not include YAML.");
    window.sessionStorage.setItem(suggestionStorageKey(), yaml);
    router.push({
      name: "builder_world_edit",
      params: {
        world_id: store.state.builder.world.id,
      },
      query: {
        prefill: "suggested-mob-definition",
      },
    });
  } catch (error: any) {
    addError.value = extractError(error);
    store.commit("ui/notification_set_error", addError.value);
  } finally {
    isSuggesting.value = false;
  }
};

const addNumericField = (payload: Record<string, any>, key: string, value: unknown) => {
  if (value === "" || value === null || value === undefined) return;
  const numericValue = Number(value);
  if (Number.isFinite(numericValue)) {
    payload[key] = numericValue;
  }
};

const ratingPlaceholder = (ratingKey: string) => {
  const value = defaultRatingPercents.value[ratingKey];
  if (value === undefined) return "";
  return formatPercent(value);
};

const ratingDefaultLabel = (ratingKey: string) => {
  const value = ratingPlaceholder(ratingKey);
  return value ? `Default ${value}%` : "";
};

const directStatPlaceholder = (statKey: string) => {
  const value = defaultDirectStats.value[statKey];
  return value === undefined ? "" : String(value);
};

const directStatDefaultLabel = (statKey: string) => {
  const value = directStatPlaceholder(statKey);
  return value ? `Default ${value}` : "";
};

const previewPercent = (value: unknown) => {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return undefined;
  return numericValue * 100;
};

const fetchDefaultPreview = async () => {
  const requestId = ++defaultPreviewRequestId;
  try {
    const resp = await axios.post(suggestionEndpoint, {
      name: "a preview mob",
      slug: "preview-mob",
      type: addForm.value.type,
      level: Number(addForm.value.level || 1),
    }, {
      validateStatus: () => true,
    });
    if (requestId !== defaultPreviewRequestId) return;
    if (resp.status >= 400) {
      defaultRatingPercents.value = {};
      defaultDirectStats.value = {};
      return;
    }

    const combatPreview = resp.data?.combat_preview || {};
    const percents: Record<string, number> = {};
    for (const [ratingKey, previewKey] of Object.entries(ratingPreviewKeys)) {
      const value = previewPercent(combatPreview[previewKey]);
      if (value !== undefined) {
        percents[ratingKey] = value;
      }
    }
    defaultRatingPercents.value = percents;

    const suggestedStats = resp.data?.suggested_stats || {};
    const directStats: Record<string, number> = {};
    for (const statKey of directStatKeys) {
      const value = Number(suggestedStats[statKey]);
      if (Number.isFinite(value)) {
        directStats[statKey] = value;
      }
    }
    defaultDirectStats.value = directStats;
  } catch {
    if (requestId === defaultPreviewRequestId) {
      defaultRatingPercents.value = {};
      defaultDirectStats.value = {};
    }
  }
};

const scheduleDefaultPreview = () => {
  if (defaultPreviewTimeout) {
    clearTimeout(defaultPreviewTimeout);
  }
  if (!showAddForm.value) return;

  defaultPreviewTimeout = setTimeout(() => {
    fetchDefaultPreview();
  }, 250);
};

watch(
  () => [showAddForm.value, addForm.value.level, addForm.value.type],
  ([isOpen]) => {
    if (!isOpen) {
      defaultRatingPercents.value = {};
      defaultDirectStats.value = {};
      defaultPreviewRequestId += 1;
      if (defaultPreviewTimeout) {
        clearTimeout(defaultPreviewTimeout);
        defaultPreviewTimeout = null;
      }
      return;
    }
    scheduleDefaultPreview();
  }
);

onUnmounted(() => {
  defaultPreviewRequestId += 1;
  if (defaultPreviewTimeout) {
    clearTimeout(defaultPreviewTimeout);
  }
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.mob-suggestion-modal {
  align-items: flex-start;
  background: rgba(0, 0, 0, 0.72);
  bottom: 0;
  box-sizing: border-box;
  display: flex;
  justify-content: center;
  left: 0;
  overflow-y: auto;
  padding: 6vh 1rem;
  position: fixed;
  right: 0;
  top: 0;
  z-index: 20000;
}

.mob-suggestion-form {
  background: $color-background;
  border: 1px solid $color-background-light-border;
  box-sizing: border-box;
  max-width: 760px;
  padding: 1rem;
  width: min(100%, 760px);

  h3 {
    line-height: 15px;
    margin-bottom: 15px;
    margin-top: 0;
  }
}

.mob-suggestion-grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(4, minmax(0, 1fr));

  .form-group {
    margin-bottom: 0;
  }
}

.mob-suggestion-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 1rem;
}

.mob-suggestion-error {
  color: $color-red;
  margin-top: 1rem;
}

.mob-suggestion-default {
  color: $color-text-hex-60;
  font-size: 0.8rem;
  line-height: 1.2;
  margin-top: 0.35rem;
}

@media (max-width: 900px) {
  .mob-suggestion-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media ($desktop-site) {
  .mob-suggestion-modal {
    align-items: center;
  }
}

@media ($mobile-site) {
  .mob-suggestion-modal {
    padding: 1rem;
  }

  .mob-suggestion-grid {
    grid-template-columns: 1fr;
  }
}
</style>
