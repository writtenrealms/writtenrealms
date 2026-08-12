<template>
  <div v-if="isInstanceWorld" class="inherited-notice mb-4">
    Trainer Profiles are inherited from {{ inheritedWorld.name }} and are
    read-only in this instance.
  </div>

  <ElementList
    title="Trainer Profiles"
    :schema="listSchema"
    :endpoint="endpoint"
    :resolve_route="resolveRoute"
    filter-display="dropdown"
    mobile-filter-row
    table-variant="data"
    default-sort="-modified_ts"
    :exclude_add="!canManage"
    @add="onClickAdd"
  />
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useStore } from "vuex";
import ElementList from "@/components/elementlist/ElementList.vue";
import { formatRelativeModifiedDate } from "@/core/utils.ts";
import { trainerProfileListEndpoint } from "@/services/trainers";

const store = useStore();
const router = useRouter();
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => Boolean(inheritedWorld.value.id));
const worldId = computed(() => store.state.builder.world.id);
const canManage = computed(() => (
  !isInstanceWorld.value
  && Number(store.state.builder.world?.builder_info?.builder_rank || 0) > 2
));
const endpoint = computed(() => trainerProfileListEndpoint(worldId.value));

const resolveRoute = (profile: any) => ({
  name: "builder_trainer_profile_details",
  params: {
    world_id: worldId.value,
    trainer_profile_id: profile.id,
  },
});

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true, mobileHidden: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "ability_count", label: "Abilities", light: true, mobileHidden: true },
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
    query: { prefill: "new-trainer-profile" },
  });
};
</script>

<style lang="scss" scoped>
.inherited-notice {
  line-height: 1.45;
}
</style>
