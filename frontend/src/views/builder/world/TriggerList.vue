<template>
  <ElementList
    title="Triggers"
    :schema="listSchema"
    :filters="listFilters"
    :endpoint="endpoint"
    :resolve_route="resolveRoute"
    filter-display="dropdown"
    table-variant="data"
    default-sort="scope"
    @add="onClickAdd"
  />
</template>

<script lang="ts" setup>
import { useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const router = useRouter();

const endpoint = `/builder/worlds/${store.state.builder.world.id}/triggers/`;

const resolveRoute = (element) => {
  return {
    name: "builder_world_trigger_details",
    params: {
      world_id: store.state.builder.world.id,
      trigger_id: element.id,
    },
  };
};

const formatName = (value, trigger) => value || trigger.key;
const formatTarget = (_value, trigger) => {
  const target = trigger.target || {};
  return target.name || target.key || target.type || "";
};
const formatActive = (value) => value ? "Active" : "Inactive";

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true, format: formatName },
  { name: "scope", label: "Scope", light: true, sortable: true },
  { name: "kind", label: "Kind", light: true, sortable: true },
  { name: "target.name", label: "Target", light: true, format: formatTarget },
  { name: "match", label: "Match", light: true, sortable: true },
  { name: "event", label: "Event", light: true, sortable: true },
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
    label: "Scope",
    attr: "scope",
    filter_options: [
      { key: "room", name: "Room" },
      { key: "zone", name: "Zone" },
      { key: "world", name: "World" },
    ],
  },
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
      world_id: store.state.builder.world.id,
    },
    query: {
      prefill: "new-trigger",
    },
  });
};
</script>
