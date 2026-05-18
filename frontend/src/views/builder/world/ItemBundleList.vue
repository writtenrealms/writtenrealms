<template>
  <div v-if="store.state.builder.world.instance_of.id">
    <h2 class="mb-4">ITEM BUNDLES</h2>
    <p>The item bundles of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_item_bundle_list', params: { world_id: store.state.builder.world.instance_of.id } }"
      >
        {{ store.state.builder.world.instance_of.name }} Item Bundles
      </router-link>
    </p>
  </div>

  <ElementList
    v-else
    title="Item Bundles"
    :schema="listSchema"
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

const endpoint = `/builder/worlds/${route.params.world_id}/itembundles/`;

const resolveRoute = element => {
  return {
    name: "builder_item_bundle_details",
    params: {
      world_id: store.state.builder.world.id,
      item_bundle_id: element.id,
    },
  };
};

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "entry_count", label: "Entries", light: true },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate,
  },
];

const onClickAdd = () => {
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: store.state.builder.world.id,
    },
    query: {
      prefill: "new-item-bundle",
    },
  });
};
</script>
