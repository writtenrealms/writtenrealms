<template>
  <div v-if="isInstanceWorld">
    <h2 class="mb-4">ABILITIES</h2>
    <p>The abilities of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_world_ability_list', params: { world_id: inheritedWorld.id } }"
      >
        {{ inheritedWorld.name }} Abilities
      </router-link>
    </p>
  </div>

  <ElementList
    v-else
    title="Abilities"
    :schema="listSchema"
    :filters="listFilters"
    :endpoint="endpoint"
    :resolve_route="resolveRoute"
    filter-display="dropdown"
    table-variant="data"
    default-sort="slug"
    @add="onClickAdd"
  />
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

const endpoint = `/builder/worlds/${store.state.builder.world.id}/abilities/`;

const resolveRoute = (element) => {
  return {
    name: "builder_world_ability_details",
    params: {
      world_id: store.state.builder.world.id,
      ability_id: element.id,
    },
  };
};

const formatCommands = (value) => {
  if (!Array.isArray(value)) return "";
  return value.join(", ");
};

const formatActionType = (value) => {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const formatTarget = (value) => {
  if (!value || typeof value !== "object") return "";
  const parts = [value.type, value.default].filter(Boolean);
  return parts.join(" / ");
};

const formatStatus = (value) => value ? "Active" : "Inactive";
const formatComponentCount = (_value, ability) => {
  const count = Array.isArray(ability.components) ? ability.components.length : 0;
  return `${count}`;
};

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "command_verbs", label: "Commands", light: true, format: formatCommands },
  { name: "action_type", label: "Action Type", light: true, sortable: true, format: formatActionType },
  { name: "target", label: "Target", light: true, format: formatTarget },
  { name: "components", label: "Components", light: true, format: formatComponentCount },
  { name: "is_active", label: "Status", light: true, sortable: true, format: formatStatus },
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
    label: "Action Type",
    attr: "action_type",
    filter_options: [
      { key: "primary", name: "Primary" },
      { key: "utility", name: "Utility" },
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
      prefill: "new-ability",
    },
  });
};
</script>
