<template>
  <div v-if="isInstanceWorld">
    <h2 class="mb-4">FACTIONS</h2>
    <p>The factions of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_world_faction_list', params: { world_id: inheritedWorld.id } }"
      >
        {{ inheritedWorld.name }} Factions
      </router-link>
    </p>
  </div>

  <div v-else-if="store.state.builder.world.builder_info.builder_rank > 2">
    <ElementList
      title="Factions"
      :schema="listSchema"
      :filters="listFilters"
      :endpoint="endpoint"
      :resolve_route="resolveRoute"
      filter-display="dropdown"
      table-variant="data"
      default-sort="-modified_ts"
      @add="onClickAdd"
    />
  </div>

  <div v-else>
    You do not have permission to manage factions for this world.
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const router = useRouter();

const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => !!inheritedWorld.value.id);
const endpoint = `/builder/worlds/${store.state.builder.world.id}/factions/`;

const resolveRoute = (element) => {
  return {
    name: "builder_world_faction_details",
    params: {
      world_id: store.state.builder.world.id,
      faction_id: element.id,
    },
  };
};

const formatFactionType = (value) => {
  if (value === "core") return "Core";
  if (value === "reputation") return "Reputation";
  return value || "";
};
const formatBoolean = (value) => value ? "Yes" : "No";
const formatLanguages = (value) => Array.isArray(value) ? value.join(", ") : "";
const formatRanks = (_value, faction) => Array.isArray(faction.ranks) ? String(faction.ranks.length) : "0";

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "code", label: "Code", nowrap: true, sortable: true },
  { name: "type", label: "Type", light: true, sortable: true, format: formatFactionType },
  { name: "playable", label: "Playable", light: true, sortable: true, format: formatBoolean },
  { name: "default_languages", label: "Languages", light: true, format: formatLanguages },
  { name: "ranks", label: "Ranks", light: true, format: formatRanks },
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
    filter_options: [
      { key: "core", name: "Core" },
      { key: "reputation", name: "Reputation" },
    ],
  },
  {
    label: "Playable",
    attr: "playable",
    filter_options: [
      { key: "true", name: "Playable" },
      { key: "false", name: "Not Playable" },
    ],
  },
];

const onClickAdd = () => {
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: store.state.builder.world.id,
    },
    query: {
      prefill: "new-faction",
    },
  });
};
</script>
