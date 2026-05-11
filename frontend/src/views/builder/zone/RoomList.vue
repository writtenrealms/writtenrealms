<template>
  <ElementList
    title="Zone Rooms"
    :schema="list_schema"
    :endpoint="endpoint"
    :resolve_route="resolve_route"
    :exclude_add="true"
    default-sort="-modified_ts"
  />
</template>

<script lang='ts' setup>
import { useRoute } from "vue-router";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const route = useRoute();

const endpoint = `/builder/worlds/${route.params.world_id}/zones/${route.params.zone_id}/rooms/`;
const resolve_route = element => {
  return {
    name: "builder_room_index",
    params: {
      world_id: route.params.world_id,
      room_id: element.id
    }
  };
};

const list_schema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate
  }
];
</script>
