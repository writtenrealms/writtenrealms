<template>
  <CraftingManifestDetails
    :world-id="worldId"
    :resource-id="recipeId"
    resource-label="crafting recipe"
    resource-title="Crafting recipe"
    list-label="Crafting Recipes"
    expected-kind="craftingrecipe"
    response-field="crafting_recipe"
    list-route-name="builder_world_crafting_recipe_list"
    detail-route-name="builder_world_crafting_recipe_details"
    detail-id-param="crafting_recipe_id"
    :load-resource="loadResource"
    :inherited-world="inheritedWorld"
  >
    <template #header="{ resource: recipe }">
      <h2 class="definition-title">{{ recipe.name || recipe.slug }}</h2>
      <div class="definition-meta color-text-60">
        ID: {{ recipe.id }} | Slug: {{ recipe.slug }} | Group: {{ recipe.group || "None" }} |
        Order: {{ recipe.order }}
      </div>
    </template>

    <template #summary="{ resource: recipe }">
      <div class="relationship-grid">
        <div class="relationship-block">
          <span class="relationship-label">Output</span>
          <router-link
            v-if="recipe.output_item_definition?.id"
            :to="{
              name: 'builder_item_definition_details',
              params: {
                world_id: worldId,
                item_definition_id: recipe.output_item_definition.id,
              },
            }"
          >
            {{ recipe.output_item_definition.name || recipe.output_item_definition.slug }}
          </router-link>
          <span v-else class="color-text-60">Unavailable</span>
        </div>

        <div class="relationship-block">
          <span class="relationship-label">Inputs</span>
          <div v-if="recipe.inputs?.length" class="relationship-values">
            <span v-for="input in recipe.inputs" :key="input.material" class="relationship-chip">
              {{ referenceName(input.material) }} × {{ input.quantity }}
            </span>
          </div>
          <span v-else class="color-text-60">None</span>
        </div>

        <div class="relationship-block compact-block">
          <span class="relationship-label">Conditions</span>
          <span>{{ hasConditions(recipe.conditions) ? "Configured" : "None" }}</span>
        </div>
      </div>
    </template>
  </CraftingManifestDetails>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import CraftingManifestDetails from "@/components/builder/world/CraftingManifestDetails.vue";
import { fetchCraftingRecipe } from "@/services/crafting";

const route = useRoute();
const store = useStore();
const worldId = computed(() => String(route.params.world_id));
const recipeId = computed(() => String(route.params.crafting_recipe_id));
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const loadResource = () => fetchCraftingRecipe(worldId.value, recipeId.value);

const referenceName = (reference: unknown) => String(reference || "")
  .replace(/^[^.]+\./, "")
  .replace(/-/g, " ")
  .replace(/\b\w/g, (letter) => letter.toUpperCase());

const hasConditions = (conditions: unknown) => (
  Boolean(conditions)
  && typeof conditions === "object"
  && Object.keys(conditions as Record<string, unknown>).length > 0
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.definition-title {
  margin-bottom: 0.35rem;
}

.definition-meta {
  min-width: 0;
  overflow-wrap: anywhere;
}

.relationship-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem 1.5rem;
  margin-top: 0.75rem;
}

.relationship-block {
  display: flex;
  flex: 1 1 18rem;
  flex-direction: column;
  gap: 0.3rem;
  min-width: 0;
}

.compact-block {
  flex-grow: 0;
}

.relationship-label {
  color: $color-text-hex-60;
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.relationship-values {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.relationship-chip {
  border: 1px solid $color-form-border;
  padding: 0.2rem 0.4rem;
}
</style>
