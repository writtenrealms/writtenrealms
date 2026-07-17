<template>
  <CraftingManifestDetails
    :world-id="worldId"
    :resource-id="profileId"
    resource-label="crafting profile"
    resource-title="Crafting profile"
    list-label="Crafting Profiles"
    expected-kind="craftingprofile"
    response-field="crafting_profile"
    list-route-name="builder_world_crafting_profile_list"
    detail-route-name="builder_world_crafting_profile_details"
    detail-id-param="crafting_profile_id"
    :load-resource="loadResource"
    :inherited-world="inheritedWorld"
  >
    <template #header="{ resource: profile }">
      <h2 class="definition-title">{{ profile.name || profile.slug }}</h2>
      <div class="definition-meta color-text-60">
        ID: {{ profile.id }} | Slug: {{ profile.slug }}
      </div>
    </template>

    <template #summary="{ resource: profile }">
      <div class="profile-summary">
        <div>
          <span class="summary-label">Keywords</span>
          <span>{{ profile.keywords || "None" }}</span>
        </div>

        <details class="recipe-relationships">
          <summary>{{ profile.recipes?.length || 0 }} recipes</summary>
          <div v-if="profile.recipes?.length" class="recipe-references">
            <span v-for="recipe in profile.recipes" :key="recipe" class="recipe-chip">
              {{ referenceName(recipe) }}
            </span>
          </div>
          <div v-else class="color-text-60">No recipes assigned.</div>
        </details>
      </div>
    </template>
  </CraftingManifestDetails>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import CraftingManifestDetails from "@/components/builder/world/CraftingManifestDetails.vue";
import { fetchCraftingProfile } from "@/services/crafting";

const route = useRoute();
const store = useStore();
const worldId = computed(() => String(route.params.world_id));
const profileId = computed(() => String(route.params.crafting_profile_id));
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const loadResource = () => fetchCraftingProfile(worldId.value, profileId.value);

const referenceName = (reference: unknown) => String(reference || "")
  .replace(/^[^.]+\./, "")
  .replace(/-/g, " ")
  .replace(/\b\w/g, (letter) => letter.toUpperCase());
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

.profile-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1.5rem;
  margin-top: 0.75rem;

  > div {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
}

.summary-label {
  color: $color-text-hex-60;
  display: block;
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.recipe-relationships {
  flex: 1 1 30rem;
  min-width: 0;

  summary {
    cursor: pointer;
  }
}

.recipe-references {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.5rem;
}

.recipe-chip {
  border: 1px solid $color-form-border;
  padding: 0.2rem 0.4rem;
}
</style>
