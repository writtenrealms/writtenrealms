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
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";
import { builderRoomIndexRoute } from "@/core/builderRoutes";

const route = useRoute();
const store = useStore();

const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/zones/${store.state.builder.zone.id}/rooms/`
));
const resolve_route = element => {
  return builderRoomIndexRoute(route.params.world_id, element);
};

const list_schema: any[] = [
  { name: "manifest_ref", label: "Room", nowrap: true },
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
