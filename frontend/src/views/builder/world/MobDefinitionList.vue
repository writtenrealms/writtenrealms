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

  <ElementList
    v-else
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

<script lang="ts" setup>
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const route = useRoute();
const router = useRouter();

const endpoint = `/builder/worlds/${route.params.world_id}/mobdefinitions/`;

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
    filter_options: [
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
      prefill: "new-mob-definition",
    },
  });
};
</script>
