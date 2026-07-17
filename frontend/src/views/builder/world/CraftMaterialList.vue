<template>
  <div v-if="isInstanceWorld">
    <h2 class="mb-4">CRAFT MATERIALS</h2>
    <p>The craft materials of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_world_craft_material_list', params: { world_id: inheritedWorld.id } }"
      >
        {{ inheritedWorld.name }} Craft Materials
      </router-link>
    </p>
  </div>

  <CraftingResourceList
    v-else
    title="Craft Materials"
    :schema="listSchema"
    :endpoint="endpoint"
    :resolve-route="resolveRoute"
    @add="onClickAdd"
  />
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useStore } from "vuex";
import CraftingResourceList from "@/components/builder/world/CraftingResourceList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";
import { craftMaterialListEndpoint } from "@/services/crafting";

const store = useStore();
const router = useRouter();
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => Boolean(inheritedWorld.value.id));
const worldId = computed(() => store.state.builder.world.id);
const endpoint = computed(() => craftMaterialListEndpoint(worldId.value));

const resolveRoute = (material: any) => ({
  name: "builder_world_craft_material_details",
  params: {
    world_id: worldId.value,
    craft_material_id: material.id,
  },
});

const formatDescription = (value: unknown) => {
  const description = String(value || "").trim();
  return description.length > 64 ? `${description.slice(0, 61)}...` : description;
};
const formatNumber = (value: unknown) => String(value ?? "");

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "order", label: "Order", light: true, sortable: true, format: formatNumber },
  { name: "description", label: "Description", light: true, format: formatDescription },
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
    params: { world_id: worldId.value },
    query: { prefill: "new-craft-material" },
  });
};
</script>
