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
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const route = useRoute();
const router = useRouter();
const store = useStore();

const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/rooms/${store.state.builder.room.id}/triggers/`
));

const resolveRoute = (element) => {
  return {
    name: "builder_room_trigger_details",
    params: {
      world_id: route.params.world_id,
      room_relative_id: route.params.room_relative_id,
      trigger_id: element.id,
    },
  };
};

const formatName = (value, trigger) => value || trigger.key;

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true, format: formatName },
  { name: "kind", label: "Kind", light: true, sortable: true },
  { name: "event", label: "Event", light: true, sortable: true },
  { name: "match", label: "Match", light: true, sortable: true },
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
      { key: "policy", name: "Policy" },
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
      room_ref: store.state.builder.room.manifest_ref,
    },
  });
};
</script>
