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
import { onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";

const store = useStore();
const route = useRoute();
const router = useRouter();

const endpoint = `/builder/worlds/${route.params.world_id}/zones/${route.params.zone_id}/spawn-plans/`;

const resolveRoute = (element: any) => ({
  name: "builder_zone_spawn_plan_details",
  params: {
    world_id: route.params.world_id,
    zone_id: route.params.zone_id,
    spawn_plan_id: element.id,
  },
});

const formatBoolean = (value: boolean) => value ? "Yes" : "No";

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "zone_ref", label: "Zone Ref", nowrap: true },
  { name: "num_entries", label: "Entries" },
  { name: "is_active", label: "Active", light: true, format: formatBoolean },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate,
  },
];

const ensureRouteZone = async () => {
  if (String(store.state.builder.zone?.id || "") === String(route.params.zone_id)) return;
  await store.dispatch("builder/zone_fetch", {
    world_id: route.params.world_id,
    zone_id: route.params.zone_id,
  });
};

const onClickAdd = () => {
  router.push({
    name: "builder_zone_spawn_plan_details",
    params: {
      world_id: route.params.world_id,
      zone_id: route.params.zone_id,
      spawn_plan_id: "new",
    },
  });
};

onMounted(ensureRouteZone);
</script>
