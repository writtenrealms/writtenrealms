<template>
  <ElementList
    title="Currently Playing"
    :schema="list_schema"
    :endpoint="endpoint"
    :resolve_route="resolve_route"
    :exclude_add="true"
  />
</template>

<script lang="ts" setup>
import ElementList from "@/components/elementlist/ElementList.vue";

const endpoint = `/staff/playing/`;
const resolve_route = (player: any) => {
  return {
    name: "builder_world_player_details",
    params: {
      world_id: player.root_world_id,
      player_id: player.id
    }
  };
};

const list_schema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "level", label: "Level", light: true, sortable: true },
  { name: "world_name", label: "World", light: true, sortable: true, sortKey: "world__name" },
  { name: "user.email", label: "Email", light: true, nowrap: true, sortable: true, sortKey: "user__email" }
];
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
