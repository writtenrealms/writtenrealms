<template>
  <div class="crafting-resource-list">
    <div v-if="loading" class="resource-state color-text-60" aria-live="polite">
      Loading {{ title.toLowerCase() }}...
    </div>

    <template v-else>
      <div class="list-header">
        <div class="left-side">
          <h2>{{ title.toUpperCase() }}</h2>

          <div class="pagination-or-search">
            <div v-if="showSearch" class="form-group">
              <input
                ref="searchInput"
                v-model="searchText"
                type="text"
                :aria-label="`Search ${title}`"
              />
            </div>
            <Pagination
              v-if="totalPages > 1"
              :page-num="pageNum"
              :total-pages="totalPages"
              @set-page="onSetPage"
            />
          </div>
        </div>

        <div class="actions">
          <button
            class="btn-small search-button"
            :aria-label="showSearch ? `Close ${title} search` : `Search ${title}`"
            @click="toggleSearch"
          >
            <span class="search-symbol" aria-hidden="true">&#9906;</span>
          </button>
          <button v-if="!excludeAdd" class="btn-small add-button" @click="emit('add')">
            ADD
          </button>
        </div>
      </div>

      <div v-if="filters.length" class="resource-filter-bar">
        <div v-for="filter in filters" :key="filter.attr" class="form-group resource-filter-input">
          <label :for="`filter-${filter.attr}`">{{ filter.label }}</label>
          <input
            :id="`filter-${filter.attr}`"
            :value="filterValues[filter.attr] || ''"
            type="text"
            :placeholder="filter.placeholder || ''"
            @input="onFilterInput(filter.attr, $event)"
          />
        </div>
      </div>

      <div v-if="errorMessage" class="resource-error" role="alert">
        <div>{{ errorMessage }}</div>
        <button class="btn-thin" @click="fetchData">RETRY</button>
      </div>

      <ElementTable
        v-else-if="elements.length"
        :title="title"
        :elements="elements"
        :schema="schema"
        variant="data"
        :sort-by="sortBy"
        @sort="onSort"
      />

      <div v-else class="no-records">No {{ title.toLowerCase() }} defined.</div>
    </template>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import ElementTable from "@/components/elementlist/ElementTable.vue";
import Pagination from "@/components/elementlist/Pagination.vue";
import { craftingApiErrorMessage } from "@/services/crafting";

const props = withDefaults(defineProps<{
  title: string;
  schema: any[];
  endpoint: string;
  resolveRoute: (element: any) => any;
  defaultSort?: string;
  excludeAdd?: boolean;
  filters?: Array<{
    label: string;
    attr: string;
    placeholder?: string;
  }>;
}>(), {
  defaultSort: "",
  excludeAdd: false,
  filters: () => [],
});

const emit = defineEmits<{
  (event: "add"): void;
}>();

const router = useRouter();
const route = useRoute();
const loading = ref(true);
const errorMessage = ref("");
const results = ref<any[]>([]);
const resultCount = ref(0);
const pageNum = ref(1);
const sortBy = ref(props.defaultSort);
const showSearch = ref(false);
const searchText = ref("");
const searchInput = ref<HTMLInputElement | null>(null);
const filterValues = ref<Record<string, string>>({});
let requestNumber = 0;
let hasLoaded = false;
let searchTimeout: ReturnType<typeof setTimeout> | null = null;
let filterTimeout: ReturnType<typeof setTimeout> | null = null;

const queryValue = (value: unknown): string => {
  const scalarValue = Array.isArray(value) ? value[0] : value;
  return typeof scalarValue === "string" ? scalarValue : "";
};

const filterAttrs = computed(() => props.filters.map(filter => filter.attr));

const allowedSortKeys = computed(() => new Set(
  props.schema
    .map(field => field.sortKey || (field.sortable ? field.name : ""))
    .filter(Boolean),
));

const normalizedSortValue = (value: string): string => {
  if (!value) return props.defaultSort;
  const sortKey = value.startsWith("-") ? value.slice(1) : value;
  return allowedSortKeys.value.has(sortKey) ? value : props.defaultSort;
};

const syncStateFromRoute = () => {
  const parsedPage = Number.parseInt(queryValue(route.query.page), 10);
  pageNum.value = Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  sortBy.value = normalizedSortValue(queryValue(route.query.sort_by));

  const routeSearchText = queryValue(route.query.query);
  searchText.value = routeSearchText;
  if (routeSearchText) {
    showSearch.value = true;
  }

  const routeFilters: Record<string, string> = {};
  for (const attr of filterAttrs.value) {
    const value = queryValue(route.query[attr]);
    if (value) {
      routeFilters[attr] = value;
    }
  }
  filterValues.value = routeFilters;
};

const replaceListQuery = async (updates: Record<string, string | null>) => {
  const nextQuery = { ...route.query };

  if (searchText.value.trim()) {
    nextQuery.query = searchText.value;
  } else {
    delete nextQuery.query;
  }

  for (const attr of filterAttrs.value) {
    const value = filterValues.value[attr] || "";
    if (value.trim()) {
      nextQuery[attr] = value;
    } else {
      delete nextQuery[attr];
    }
  }

  for (const [key, value] of Object.entries(updates)) {
    if (value) {
      nextQuery[key] = value;
    } else {
      delete nextQuery[key];
    }
  }

  await router.replace({ query: nextQuery });
};

const elements = computed(() => results.value.map((element) => ({
  ...element,
  link: router.resolve(props.resolveRoute(element)).href,
})));

const totalPages = computed(() => Math.max(1, Math.ceil(resultCount.value / 10)));

const fetchData = async () => {
  const activeRequest = ++requestNumber;
  if (!hasLoaded) {
    loading.value = true;
  }
  errorMessage.value = "";

  const params: Record<string, string | number> = { page: pageNum.value };
  if (searchText.value.trim()) {
    params.query = searchText.value.trim();
  }
  if (sortBy.value) {
    params.sort_by = sortBy.value;
  }
  for (const [attr, value] of Object.entries(filterValues.value)) {
    if (value.trim()) params[attr] = value.trim();
  }

  try {
    const response = await axios.get(props.endpoint, { params });
    if (activeRequest !== requestNumber) return;
    const payload = response.data || {};
    results.value = Array.isArray(payload.results) ? payload.results : [];
    resultCount.value = Number(payload.count) || 0;
  } catch (error: unknown) {
    if (activeRequest !== requestNumber) return;
    if (axios.isAxiosError(error) && error.response?.status === 404 && pageNum.value > 1) {
      await replaceListQuery({ page: null });
      return;
    }
    results.value = [];
    resultCount.value = 0;
    errorMessage.value = craftingApiErrorMessage(
      error,
      `Could not load ${props.title.toLowerCase()}.`,
    );
  } finally {
    if (activeRequest === requestNumber) {
      hasLoaded = true;
      loading.value = false;
    }
  }
};

const onSetPage = (nextPage: number) => {
  void replaceListQuery({
    page: nextPage > 1 ? String(nextPage) : null,
  });
};

const onSort = (sortKey: string) => {
  if (!sortKey) return;
  const descendingKey = `-${sortKey}`;
  let nextSort = sortKey;
  if (sortBy.value === sortKey) {
    nextSort = descendingKey;
  }
  void replaceListQuery({
    sort_by: nextSort === props.defaultSort ? null : nextSort,
    page: null,
  });
};

const onFilterInput = (attr: string, event: Event) => {
  const target = event.target as HTMLInputElement | null;
  filterValues.value = {
    ...filterValues.value,
    [attr]: target?.value || "",
  };
  if (filterTimeout) clearTimeout(filterTimeout);
  filterTimeout = setTimeout(() => {
    void replaceListQuery({ page: null });
  }, 250);
};

const toggleSearch = async () => {
  showSearch.value = !showSearch.value;
  if (!showSearch.value) {
    searchText.value = "";
    await replaceListQuery({ query: null, page: null });
    return;
  }
  await nextTick();
  searchInput.value?.focus();
};

watch(searchText, (value) => {
  if (value === queryValue(route.query.query)) return;
  if (searchTimeout) clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    if (value !== queryValue(route.query.query)) {
      void replaceListQuery({
        query: value || null,
        page: null,
      });
    }
  }, 250);
});

watch(
  [
    () => route.query,
    () => props.endpoint,
    () => props.defaultSort,
    () => filterAttrs.value.join("\u0000"),
    () => [...allowedSortKeys.value].join("\u0000"),
  ],
  () => {
    if (searchTimeout) {
      clearTimeout(searchTimeout);
      searchTimeout = null;
    }
    if (filterTimeout) {
      clearTimeout(filterTimeout);
      filterTimeout = null;
    }
    syncStateFromRoute();
    void fetchData();
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  requestNumber += 1;
  if (searchTimeout) clearTimeout(searchTimeout);
  if (filterTimeout) clearTimeout(filterTimeout);
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.crafting-resource-list {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;
}

.resource-state {
  align-items: center;
  display: flex;
  justify-content: center;
  min-height: 12rem;
}

.list-header {
  align-items: center;
  box-sizing: border-box;
  display: flex;
  gap: 1rem;
  justify-content: space-between;
  min-height: 36px;
  min-width: 0;
  width: 100%;
}

.left-side {
  align-items: center;
  display: flex;
  flex: 1 1 auto;
  min-width: 0;

  h2 {
    flex: 1 1 auto;
    min-width: 0;
    overflow-wrap: anywhere;
  }
}

.pagination-or-search {
  align-items: center;
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
  min-width: 0;

  .form-group {
    flex: 0 1 22rem;
    margin: 0;
    max-width: 100%;
    min-width: 0;
    width: 22rem;
  }

  input {
    max-width: 100%;
    min-width: 0;
  }
}

.actions {
  display: flex;
  flex: 0 0 auto;
}

.resource-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0.75rem 0;
}

.resource-filter-input {
  margin: 0;
  max-width: 18rem;

  input {
    box-sizing: border-box;
    width: 100%;
  }
}

.search-button {
  margin: 0 5px;
}

.search-symbol {
  display: block;
  font-size: 1em;
  transform: rotate(45deg);
}

.resource-error {
  align-items: flex-start;
  border: 1px solid $color-form-border;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 1rem;
  padding: 1rem;
}

.no-records {
  margin-top: 20px;
}

@media ($mobile-site) {
  .list-header,
  .left-side {
    align-items: stretch;
    flex-direction: column;
  }

  .actions {
    align-self: flex-end;
    flex-direction: column;
  }

  .pagination-or-search {
    flex-wrap: wrap;
    justify-content: flex-start;

    .form-group {
      flex-basis: 100%;
      width: 100%;
    }
  }

  .search-button {
    margin: 5px 0 0;
    order: 2;
  }

  .add-button {
    order: 1;
  }
}
</style>
