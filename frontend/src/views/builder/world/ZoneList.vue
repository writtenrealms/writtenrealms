<template>
  <ElementList
    title="Zones"
    :schema="list_schema"
    :endpoint="endpoint"
    :resolve_route="resolve_route"
    default-sort="name"
    @add="onClickAdd" />
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { useRoute } from "vue-router";
import ElementList from "@/components/elementlist/ElementList.vue";
import { BUILDER_FORMS } from "@/core/forms.ts";
import { formatRelativeModifiedDate } from "@/core/utils.ts";
import { builderZoneIndexRoute } from "@/core/builderRoutes";

const store = useStore();
const route = useRoute();

const endpoint = computed(() => `/builder/worlds/${route.params.world_id}/zones/`);
const resolve_route = element => {
  return builderZoneIndexRoute(route.params.world_id, element);
};

const list_schema: any[] = [
  { name: "manifest_ref", label: "Zone", nowrap: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "num_rooms", label: "Rooms" },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate
  }
];

const onClickAdd = () => {
  const new_zone = {
    name: "Unnamed Zone"
  };

  store.commit('ui/modal/open_form', {
    title: `Add Zone`,
    data: new_zone,
    schema: BUILDER_FORMS.ZONE_INFO,
    action: 'builder/zone_create',
  });
};
</script>
