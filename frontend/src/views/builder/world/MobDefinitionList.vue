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
    <form
      v-if="showAddForm"
      class="mob-suggestion-form mb-4"
      @submit.prevent="createSuggestedMob"
    >
      <h3>Add Mob</h3>
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
          <label for="mob-suggestion-level">Mob level</label>
          <input
            id="mob-suggestion-level"
            v-model.number="addForm.level"
            type="number"
            min="1"
            required
          />
        </div>
      </div>
      <div v-if="addError" class="mob-suggestion-error mb-3">{{ addError }}</div>
      <div class="mob-suggestion-actions">
        <button class="btn-small" type="submit" :disabled="isSuggesting">
          GENERATE YAML
        </button>
        <button class="btn-small button-gray ml-2" type="button" :disabled="isSuggesting" @click="cancelAdd">
          CANCEL
        </button>
      </div>
    </form>

    <ElementList
      title="Mobs"
      :schema="listSchema"
      :filters="listFilters"
      :endpoint="endpoint"
      :resolve_route="resolveRoute"
      filter-display="dropdown"
      table-variant="data"
      default-sort="-modified_ts"
      @add="onClickAdd"
    />
  </template>
</template>

<script lang="ts" setup>
import axios from "axios";
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

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
const addForm = ref({
  name: "a new mob",
  slug: "new-mob",
  type: "beast",
  level: 1,
});

const resolveRoute = element => {
  return {
    name: "builder_mob_definition_details",
    params: {
      world_id: store.state.builder.world.id,
      mob_definition_id: element.id,
    },
  };
};

const formatBoolean = value => value ? "Yes" : "No";

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "type", label: "Type", light: true, sortable: true, sortKey: "mob_type" },
  { name: "randomized", label: "Randomized", light: true, format: formatBoolean },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate,
  },
];

const listFilters: any[] = [
  {
    label: "Type",
    attr: "type",
    filter_options: mobTypeOptions,
  },
];

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
    const resp = await axios.post(suggestionEndpoint, {
      name: addForm.value.name,
      slug: addForm.value.slug,
      type: addForm.value.type,
      level: Number(addForm.value.level || 1),
    });
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
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.mob-suggestion-form {
  border: 1px solid $color-background-light-border;
  padding: 1rem;

  h3 {
    margin-top: 0;
  }
}

.mob-suggestion-grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.mob-suggestion-actions {
  display: flex;
  align-items: center;
}

.mob-suggestion-error {
  color: $color-red;
}

@media (max-width: 900px) {
  .mob-suggestion-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .mob-suggestion-grid {
    grid-template-columns: 1fr;
  }
}
</style>
