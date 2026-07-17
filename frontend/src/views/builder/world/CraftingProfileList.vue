<template>
  <div v-if="isInstanceWorld">
    <h2 class="mb-4">CRAFTING PROFILES</h2>
    <p>The crafting profiles of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_world_crafting_profile_list', params: { world_id: inheritedWorld.id } }"
      >
        {{ inheritedWorld.name }} Crafting Profiles
      </router-link>
    </p>
  </div>

  <CraftingResourceList
    v-else
    title="Crafting Profiles"
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
import { craftingProfileListEndpoint } from "@/services/crafting";

const store = useStore();
const router = useRouter();
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => Boolean(inheritedWorld.value.id));
const worldId = computed(() => store.state.builder.world.id);
const endpoint = computed(() => craftingProfileListEndpoint(worldId.value));

const resolveRoute = (profile: any) => ({
  name: "builder_world_crafting_profile_details",
  params: {
    world_id: worldId.value,
    crafting_profile_id: profile.id,
  },
});

const formatKeywords = (value: unknown) => String(value || "").trim().replace(/\s+/g, ", ");
const formatNumber = (value: unknown) => String(value ?? "");

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "keywords", label: "Keywords", light: true, format: formatKeywords },
  { name: "recipe_count", label: "Recipes", light: true, format: formatNumber },
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
    query: { prefill: "new-crafting-profile" },
  });
};
</script>
