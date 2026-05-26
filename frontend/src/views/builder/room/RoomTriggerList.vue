<template>
  <ElementList
    title="Room Triggers"
    :schema="listSchema"
    :filters="listFilters"
    :endpoint="endpoint"
    :resolve_route="resolveRoute"
    filter-display="dropdown"
    table-variant="data"
    default-sort="order"
    @add="onClickAdd"
  />
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const route = useRoute();
const router = useRouter();

const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/rooms/${route.params.room_id}/triggers/`
));

const resolveRoute = (element) => {
  return {
    name: "builder_room_trigger_details",
    params: {
      world_id: route.params.world_id,
      room_id: route.params.room_id,
      trigger_id: element.id,
    },
  };
};

const formatName = (value, trigger) => value || trigger.key;
const formatActive = (value) => value ? "Active" : "Inactive";

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true, format: formatName },
  { name: "kind", label: "Kind", light: true, sortable: true },
  { name: "match", label: "Match", light: true, sortable: true },
  { name: "order", label: "Order", light: true, sortable: true },
  { name: "gate_delay", label: "Delay", light: true, sortable: true },
  { name: "is_active", label: "Status", light: true, sortable: true, format: formatActive },
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
    label: "Kind",
    attr: "kind",
    filter_options: [
      { key: "command", name: "Command" },
      { key: "event", name: "Event" },
    ],
  },
  {
    label: "Status",
    attr: "is_active",
    filter_options: [
      { key: "true", name: "Active" },
      { key: "false", name: "Inactive" },
    ],
  },
];

const onClickAdd = () => {
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: route.params.world_id,
    },
    query: {
      prefill: "new-room-trigger",
      room_id: String(route.params.room_id),
    },
  });
};
</script>
