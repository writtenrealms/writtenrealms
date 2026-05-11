<template>
  <template v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <ElementList
      title="Players"
      :schema="list_schema"
      :filters="list_filters"
      :endpoint="endpoint"
      :resolve_route="resolve_route"
      default-sort="-modified_ts"
    />
  </template>
  <template v-else>
    <div>
      You do not have permission to manage players for this world.
    </div>
  </template>
</template>

<script lang="ts" setup>
import { useStore } from "vuex";
import { useRoute } from "vue-router";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const route = useRoute();

const endpoint = `/builder/worlds/${route.params.world_id}/players/`;

const resolve_route = (element: any) => {
  return {
    name: "builder_world_player_details",
    params: {
      world_id: route.params.world_id,
      player_id: element.id,
    },
  };
};

const list_schema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "level", label: "Level", light: true, sortable: true },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate
  },
];

const list_filters: any[] = [
  {
    label: "Faction",
    attr: "core_faction",
    filter_options: [
      { key: "human", name: "Human" },
      { key: "orc", name: "Orc" },
      { key: "lifeless", name: "Lifeless" }
    ]
  },
  {
    label: "Level",
    attr: "level_range",
    filter_options: [
      { key: "15", name: "1-5" },
      { key: "610", name: "6-10" },
      { key: "1115", name: "11-15" },
      { key: "1620", name: "16-20" }
    ]
  }
];
</script>
