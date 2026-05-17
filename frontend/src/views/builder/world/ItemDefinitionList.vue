<template>
  <div v-if="store.state.builder.world.instance_of.id">
    <h2 class="mb-4">ITEMS</h2>
    <p>The items of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_item_definition_list', params: { world_id: store.state.builder.world.instance_of.id } }"
      >
        {{ store.state.builder.world.instance_of.name }} Items
      </router-link>
    </p>
  </div>

  <ElementList
    v-else
    title="Items"
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
import { useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const router = useRouter();

const endpoint = `/builder/worlds/${store.state.builder.world.id}/itemdefinitions/`;

const resolveRoute = element => {
  return {
    name: "builder_item_definition_details",
    params: {
      world_id: store.state.builder.world.id,
      item_definition_id: element.id,
    },
  };
};

const formatBoolean = value => value ? "Yes" : "No";

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "type", label: "Type", light: true, sortable: true, sortKey: "item_type" },
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
    attr: "item_type",
    filter_options: [
      { key: "equippable", name: "Equippable" },
      { key: "consumable", name: "Consumable" },
      { key: "food", name: "Food" },
      { key: "light", name: "Light" },
      { key: "container", name: "Container" },
      { key: "key", name: "Key" },
      { key: "inert", name: "Inert" },
      { key: "corpse", name: "Corpse" },
      { key: "trash", name: "Trash" },
      { key: "quest", name: "Quest" },
      { key: "ammunition", name: "Ammunition" },
      { key: "augment", name: "Augment" },
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
      prefill: "new-item-definition",
    },
  });
};
</script>
