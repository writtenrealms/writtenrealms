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

  <CraftingResourceList
    v-else
    title="Crafting Recipes"
    :schema="listSchema"
    :filters="listFilters"
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

const formatNumber = (value: unknown) => String(value ?? "");
const formatRecipeCost = (_value: unknown, recipe: any) => {
  if (recipe.money?.display) return String(recipe.money.display);
  if (recipe.money?.amount != null) {
    return `${recipe.money.amount} ${recipe.money.currency || ""}`.trim();
  }
  if (recipe.cost == null) return "None";
  const currency = typeof recipe.currency === "object"
    ? recipe.currency?.code || recipe.currency?.name
    : recipe.currency;
  return `${recipe.cost} ${currency || ""}`.trim();
};

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Output", nowrap: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "group", label: "Group", light: true, sortable: true },
  { name: "ingredient_count", label: "Inputs", light: true, format: formatNumber },
  { name: "money", label: "Fee", light: true, nowrap: true, format: formatRecipeCost },
  { name: "order", label: "Order", light: true, sortable: true, format: formatNumber },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate,
  },
];

const listFilters = [
  {
    label: "Group",
    attr: "group",
    placeholder: "Group slug",
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
