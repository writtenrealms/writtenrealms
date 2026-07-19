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

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate,
  },
];

const listFilters = computed(() => {
  const classOptions = store.state.builder.world?.class_options || [];
  if (!classOptions.length) return [];
  return [
    {
      label: "Class",
      attr: "class",
      filter_options: classOptions,
    },
  ];
});

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
