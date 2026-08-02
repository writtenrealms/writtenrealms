<template>
  <ElementList
    title="Spawn Plans"
    :schema="listSchema"
    :endpoint="endpoint"
    :resolve_route="resolveRoute"
    table-variant="data"
    default-sort="-modified_ts"
    @add="onClickAdd"
  />
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const route = useRoute();
const router = useRouter();

const zone = computed(() => store.state.builder.zone);
const endpoint = computed(() => (
  `/builder/worlds/${route.params.world_id}/zones/${zone.value.id}/spawn-plans/`
));

const resolveRoute = (element: any) => ({
  name: "builder_zone_spawn_plan_details",
  params: {
    world_id: route.params.world_id,
    zone_relative_id: route.params.zone_relative_id,
    spawn_plan_id: element.id,
  },
});

const listSchema: any[] = [
  { name: "zone_ref", label: "Zone", nowrap: true },
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

const onClickAdd = () => {
  router.push({
    name: "builder_zone_spawn_plan_details",
    params: {
      world_id: route.params.world_id,
      zone_relative_id: route.params.zone_relative_id,
      spawn_plan_id: "new",
    },
  });
};
</script>
