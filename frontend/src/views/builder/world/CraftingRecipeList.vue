<template>
  <div v-if="isInstanceWorld">
    <h2 class="mb-4">CRAFTING RECIPES</h2>
    <p>The crafting recipes of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_world_crafting_recipe_list', params: { world_id: inheritedWorld.id } }"
      >
        {{ inheritedWorld.name }} Crafting Recipes
      </router-link>
    </p>
  </div>

  <ElementList
    v-else
    class="half-width-filters"
    title="Crafting Recipes"
    :schema="listSchema"
    :filters="listFilters"
    :endpoint="endpoint"
    :resolve_route="resolveRoute"
    filter-display="dropdown"
    mobile-filter-row
    table-variant="data"
    @add="onClickAdd"
  />
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";
import { craftingRecipeListEndpoint } from "@/services/crafting";

const store = useStore();
const router = useRouter();
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => Boolean(inheritedWorld.value.id));
const worldId = computed(() => store.state.builder.world.id);
const endpoint = computed(() => craftingRecipeListEndpoint(worldId.value));

const resolveRoute = (recipe: any) => ({
  name: "builder_world_crafting_recipe_details",
  params: {
    world_id: worldId.value,
    crafting_recipe_id: recipe.id,
  },
});

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Output", nowrap: true, mobileHidden: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
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
    label: "Group",
    attr: "group",
    filter_options: [],
  },
];

const onClickAdd = () => {
  router.push({
    name: "builder_world_edit",
    params: { world_id: worldId.value },
    query: { prefill: "new-crafting-recipe" },
  });
};
</script>
