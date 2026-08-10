<template>
  <div v-if="isInstanceWorld">
    <h2 class="mb-4">MERCHANT PROFILES</h2>
    <p>The merchant profiles of an instance are inherited from the parent world:</p>
    <p>
      <router-link
        :to="{ name: 'builder_merchant_profile_list', params: { world_id: inheritedWorld.id } }"
      >
        {{ inheritedWorld.name }} Merchant Profiles
      </router-link>
    </p>
  </div>

  <ElementList
    v-else
    title="Merchant Profiles"
    :schema="listSchema"
    :filters="listFilters"
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
import { merchantProfileListEndpoint } from "@/services/merchants";

const store = useStore();
const router = useRouter();
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const isInstanceWorld = computed(() => Boolean(inheritedWorld.value.id));
const worldId = computed(() => store.state.builder.world.id);
const canManage = computed(() => (
  !isInstanceWorld.value
  && Number(store.state.builder.world?.builder_info?.builder_rank || 0) > 2
));
const endpoint = computed(() => merchantProfileListEndpoint(worldId.value));

const resolveRoute = (element: any) => {
  return {
    name: "builder_merchant_profile_details",
    params: {
      world_id: worldId.value,
      merchant_profile_id: element.id,
    },
  };
};

const formatBoolean = (value: unknown) => value ? "Yes" : "No";
const formatFundsMode = (value: unknown) => {
  if (value === "finite") return "Finite";
  if (value === "unlimited") return "Unlimited";
  return value || "";
};
const formatRestock = (value: unknown) => {
  if (!value) return "Manual";
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return value;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
};

const listSchema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true, mobileHidden: true },
  { name: "slug", label: "Slug", nowrap: true, sortable: true },
  { name: "stock_count", label: "Stock", light: true, mobileHidden: true },
  {
    name: "funds_mode",
    label: "Funds",
    light: true,
    sortable: true,
    format: formatFundsMode,
    mobileHidden: true,
  },
  {
    name: "buyback_enabled",
    label: "Buyback",
    light: true,
    format: formatBoolean,
    mobileHidden: true,
  },
  {
    name: "restock_interval_seconds",
    label: "Restock",
    light: true,
    sortable: true,
    format: formatRestock,
    mobileHidden: true,
  },
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
    label: "Funds",
    attr: "funds_mode",
    filter_options: [
      { key: "unlimited", name: "Unlimited" },
      { key: "finite", name: "Finite" },
    ],
  },
  {
    label: "Buyback",
    attr: "buyback_enabled",
    filter_options: [
      { key: "true", name: "Enabled" },
      { key: "false", name: "Disabled" },
    ],
  },
];

const onClickAdd = () => {
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: worldId.value,
    },
    query: {
      prefill: "new-merchant-profile",
    },
  });
};
</script>
