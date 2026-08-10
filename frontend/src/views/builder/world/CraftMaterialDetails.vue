<template>
  <ManifestResourceDetails
    :world-id="worldId"
    :resource-id="materialId"
    resource-label="craft material"
    resource-title="Craft material"
    list-label="Craft Materials"
    expected-kind="craftmaterial"
    response-field="craft_material"
    list-route-name="builder_world_craft_material_list"
    detail-route-name="builder_world_craft_material_details"
    detail-id-param="craft_material_id"
    :load-resource="loadResource"
    :inherited-world="inheritedWorld"
  >
    <template #header="{ resource: material }">
      <h2 class="definition-title">{{ material.name || material.slug }}</h2>
      <div class="definition-meta color-text-60">
        ID: {{ material.id }} | Slug: {{ material.slug }} | Order: {{ material.order }}
      </div>
    </template>

    <template #summary="{ resource: material }">
      <p v-if="material.description" class="definition-description">
        {{ material.description }}
      </p>
      <div v-else class="definition-description color-text-60">No description.</div>
    </template>
  </ManifestResourceDetails>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import ManifestResourceDetails from "@/components/builder/world/ManifestResourceDetails.vue";
import { fetchCraftMaterial } from "@/services/crafting";

const route = useRoute();
const store = useStore();
const worldId = computed(() => String(route.params.world_id));
const materialId = computed(() => String(route.params.craft_material_id));
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const loadResource = () => fetchCraftMaterial(worldId.value, materialId.value);
</script>

<style lang="scss" scoped>
.definition-title {
  margin-bottom: 0.35rem;
}

.definition-meta {
  min-width: 0;
  overflow-wrap: anywhere;
}

.definition-description {
  line-height: 1.45;
  margin: 0.65rem 0 0;
  max-width: 62rem;
}
</style>
