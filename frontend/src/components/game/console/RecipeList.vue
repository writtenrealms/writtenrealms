<template>
  <div class="recipe-list indented">
    <div v-if="recipes.length === 0">
      {{ emptyText }}
    </div>
    <template v-else>
      <div>Recipes at {{ providerName }}:</div>
      <ol class="list mt-4">
        <li
          v-for="recipe in recipes"
          :key="recipe.key"
          :value="recipe.number"
          class="recipe-item"
        >
          <button
            v-if="isLastMessage"
            type="button"
            class="recipe-name interactive"
            :class="[recipe.output?.quality]"
            @click="inspectRecipe(recipe)"
          >
            {{ recipe.name }}
          </button>
          <span v-else :class="[recipe.output?.quality]">{{ recipe.name }}</span>
          <span class="color-text-60"> — {{ recipeStatus(recipe) }}</span>
        </li>
      </ol>
      <div class="color-text-60 mt-4">
        Use: recipe &lt;number&gt; to inspect; craft &lt;number&gt; to make.
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";

const store = useStore();
const props = defineProps<{ message: any }>();

const recipes = computed(() => props.message.data.recipes || []);
const providers = computed(() => props.message.data.providers || []);
const providerName = computed(() => (
  providers.value.length === 1
    ? providers.value[0].name
    : "local workshops"
));
const emptyText = computed(() => (
  providers.value.length === 1
    ? `${providerName.value} offers no matching recipes.`
    : "Local workshops offer no matching recipes."
));
const isLastMessage = computed(
  () => store.state.game.last_message[props.message.type] == props.message,
);

const joinPhrases = (phrases: string[]) => {
  if (phrases.length <= 1) return phrases[0] || "materials";
  if (phrases.length === 2) return `${phrases[0]} and ${phrases[1]}`;
  return `${phrases.slice(0, -1).join(", ")}, and ${phrases[phrases.length - 1]}`;
};

const recipeStatus = (recipe: any) => {
  if (!recipe.conditions_met) return "locked";
  if (recipe.ready) return "ready";
  const missing = (recipe.inputs || [])
    .filter((entry: any) => Number(entry.missing || 0) > 0)
    .map((entry: any) => `${entry.missing} ${entry.material?.name || "material"}`);
  return `need ${joinPhrases(missing)}`;
};

const inspectRecipe = (recipe: any) => {
  const providerSuffix = providers.value.length === 1
    ? ` at ${providers.value[0].key}`
    : "";
  store.dispatch("game/cmd", `recipe ${recipe.number}${providerSuffix}`);
};
</script>

<style lang="scss" scoped>
.recipe-name {
  appearance: none;
  padding: 0;
  border: 0;
  border-bottom: 1px dotted #888;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
}
</style>
