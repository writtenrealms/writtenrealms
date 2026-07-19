<template>
  <div class="element-list">
    <div class="resource-list">
      <div v-if="loading" class="loading-screen">Loading...</div>

      <div v-else-if="elements">
        <div class="list-header">
          <div class="left-side">
            <h2>{{ title.toUpperCase() }}</h2>

            <div class="pagination-or-search">
              <div class="form-group" v-if="showSearch">
                <input
                  type="text"
                  v-model="searchText"
                  ref="search"
                  :aria-label="`Search ${title}`"
                />
              </div>
              <Pagination
                v-if="totalPages > 1"
                :pageNum="pageNum"
                :totalPages="totalPages"
                @set-page="onSetPage"
              />
            </div>
          </div>

          <div class="actions">
            <button
              class="btn-small search-button"
              :aria-label="showSearch ? `Close ${title} search` : `Search ${title}`"
              @click="onToggleSearch"
            >
              <div class="search-symbol" aria-hidden="true">&#9906;</div>
            </button>
            <button class="btn-small add-button" @click="onAdd" v-if="!exclude_add">ADD</button>
          </div>
        </div>

        <div v-if="filters?.length && filterDisplay === 'dropdown'" class="resource-filter-bar">
          <div
            v-for="filter_group in filters"
            :key="filter_group.attr"
            class="form-group resource-filter-select"
          >
            <label :for="`filter-${filter_group.attr}`">{{ filter_group.label }}</label>
            <select
              :id="`filter-${filter_group.attr}`"
              :value="selectedFilterValue(filter_group.attr)"
              @change="onFilterDropdownChange(filter_group.attr, $event)"
            >
              <option value="">All</option>
              <option
                v-for="value in filter_group.filter_options"
                :key="value.key"
                :value="value.key"
              >
                {{ value.name }}
              </option>
            </select>
          </div>
        </div>

        <ElementTable
          :title="title"
          :elements="elements"
          :totalPages="totalPages"
          :schema="schema"
          :variant="tableVariant"
          :sort-by="sortBy"
          @sort="onSort"
          v-if="elements.length"
        />
        <div v-else class="no-records">No {{ title }} defined.</div>
      </div>
    </div>

    <div v-if="filters?.length && filterDisplay === 'sidebar'" class="resource-filters">
      <FilterGroup v-for="filter_group in filters" :key="filter_group.attr" :values="filter_group.filter_options"
        :title="filter_group.label" :attr="filter_group.attr"
        :selected-value="selectedFilterValue(filter_group.attr)" @select-filter="onSelectFilter"
        @clear-filter="onClearFilter" />
    </div>
  </div>
</template>


<script lang='ts' setup>
import { ref, onBeforeUnmount, computed, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import ElementTable from "@/components/elementlist/ElementTable.vue";
import Pagination from "@/components/elementlist/Pagination.vue";
import FilterGroup from "@/components/elementlist/FilterGroup.vue";
import {
  ElementListSchema,
  ElementListFilterGroup
} from "@/core/interfaces.ts";
import axios from "axios";

const router = useRouter();
const route = useRoute();

const props = withDefaults(defineProps<{
  title: string;
  schema: Array<ElementListSchema>;
  filters?: Array<ElementListFilterGroup>;
  endpoint: string;
  resolve_route: Function;
  exclude_add?: boolean;
  filterDisplay?: "sidebar" | "dropdown";
  tableVariant?: "default" | "data";
  defaultSort?: string;
}>(), {
  filterDisplay: "dropdown",
  tableVariant: "data",
  defaultSort: "",
});
const emit = defineEmits(["add"]);

const pageNum = ref(1);
const loading = ref(true);
const paginated_data = ref<any>({ count: 0, results: [] });
const appliedFilters = ref<Record<string, string>>({});
const sortBy = ref(props.defaultSort);
const showSearch = ref(false);
const searchText = ref("");
const search = ref(null);
let timeout: ReturnType<typeof setTimeout> | null = null;

const queryValue = (value: unknown): string => {
  const scalarValue = Array.isArray(value) ? value[0] : value;
  return typeof scalarValue === "string" ? scalarValue : "";
};

const filterAttrs = computed(() => (
  (props.filters || []).map(filterGroup => filterGroup.attr)
));

const allowedSortKeys = computed(() => new Set(
  props.schema
    .map(field => field.sortKey || (field.sortable ? field.name : ""))
    .filter(Boolean),
));

const normalizedSortValue = (value: string): string => {
  if (!value) {
    return props.defaultSort;
  }
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
  appliedFilters.value = routeFilters;
};

let requestSequence = 0;
const fetchData = async () => {
  const requestId = ++requestSequence;
  const filters: Record<string, string | number> = {
    ...appliedFilters.value,
    page: pageNum.value,
  };
  if (searchText.value) {
    filters.query = searchText.value;
  }
  if (sortBy.value) {
    filters.sort_by = sortBy.value;
  }

  try {
    const response = await axios.get(props.endpoint, { params: filters });
    if (requestId === requestSequence) {
      loading.value = false;
      paginated_data.value = response.data;
    }
  } catch (error: unknown) {
    if (requestId !== requestSequence) {
      return;
    }
    if (axios.isAxiosError(error) && error.response?.status === 404 && pageNum.value > 1) {
      await replaceListQuery({ page: null });
      return;
    }
    loading.value = false;
    paginated_data.value = { count: 0, results: [] };
  }
};

const replaceListQuery = async (updates: Record<string, string | null>) => {
  const nextQuery = { ...route.query };
  if (searchText.value) {
    nextQuery.query = searchText.value;
  } else {
    delete nextQuery.query;
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

watch(
  [
    () => route.query,
    () => props.endpoint,
    () => props.defaultSort,
    () => filterAttrs.value.join("\u0000"),
    () => [...allowedSortKeys.value].join("\u0000"),
  ],
  () => {
    if (timeout) {
      clearTimeout(timeout);
      timeout = null;
    }
    syncStateFromRoute();
    void fetchData();
  },
  { immediate: true },
);

const elements = computed(() => {
  return (paginated_data.value.results || []).map(element => {
    const route_data = props.resolve_route(element);
    const resolved_route = router.resolve(route_data);
    return {
      ...element,
      link: resolved_route.href
    };
  });
});

const totalPages = computed(() => Math.ceil(paginated_data.value.count / 10));

watch(searchText, (value) => {
  if (value === queryValue(route.query.query)) {
    return;
  }
  if (timeout) {
    clearTimeout(timeout);
  }
  timeout = setTimeout(() => {
    if (value !== queryValue(route.query.query)) {
      void replaceListQuery({
        query: value || null,
        page: null,
      });
    }
  }, 250);
});

// Pagination

const onSetPage = (pageNumTo: number) => {
  void replaceListQuery({
    page: pageNumTo > 1 ? String(pageNumTo) : null,
  });
};

const onSelectFilter = (attr: string, value: string) => {
  void replaceListQuery({
    [attr]: value,
    page: null,
  });
};

const onClearFilter = (attr: string) => {
  void replaceListQuery({
    [attr]: null,
    page: null,
  });
};

const selectedFilterValue = (attr: string) => {
  return appliedFilters.value[attr] || "";
};

const onFilterDropdownChange = (attr: string, event: Event) => {
  const target = event.target as HTMLSelectElement | null;
  const value = target?.value || "";
  if (value) {
    onSelectFilter(attr, value);
  } else {
    onClearFilter(attr);
  }
};

const onSort = (sortKey: string) => {
  if (!sortKey) {
    return;
  }

  const descendingSortKey = `-${sortKey}`;
  if (sortBy.value === sortKey) {
    sortBy.value = descendingSortKey;
  } else if (sortBy.value === descendingSortKey) {
    sortBy.value = sortKey;
  } else {
    sortBy.value = sortKey;
  }

  void replaceListQuery({
    sort_by: sortBy.value === props.defaultSort ? null : sortBy.value,
    page: null,
  });
};


// Display details

const onAdd = () => {
  emit("add");
};

const onToggleSearch = () => {
  if (showSearch.value) {
    showSearch.value = false;
    searchText.value = "";
    void replaceListQuery({ query: null, page: null });
  } else {
    showSearch.value = true;
    nextTick(() => {
      const el = search.value as HTMLElement | null;
      if (el) {
        el.focus();
      }
    });
  }
};

onBeforeUnmount(() => {
  requestSequence += 1;
  if (timeout) {
    clearTimeout(timeout);
  }
});
</script>

<style lang='scss' scoped>
@import "@/styles/fonts.scss";
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.element-list {
  box-sizing: border-box;
  flex-grow: 1;
  display: flex;
  flex-direction: row;
  max-width: 100%;
  min-width: 0;
  width: 100%;

  .resource-list {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    flex-grow: 1;
    max-width: 100%;
    min-width: 0;
    width: 100%;

    > div {
      box-sizing: border-box;
      max-width: 100%;
      min-width: 0;
      width: 100%;
    }

    .loading-screen {
      display: flex;
      flex-direction: column;
      flex-grow: 1;
      justify-content: center;
      align-items: center;
    }

    .list-header {
      box-sizing: border-box;
      display: flex;
      gap: 1rem;
      justify-content: space-between;
      max-width: 100%;
      min-height: 36px;
      min-width: 0;
      align-items: center;
      width: 100%;
      //align-items: center;

      .left-side {
        display: flex;
        flex-grow: 1;
        min-width: 0;

        @media ($mobile-site) {
          flex-direction: column;
        }

        @media ($desktop-site) {
          justify-content: space-between;
          align-items: center;
        }

        h2 {
          flex-grow: 1;
          display: block;
          min-width: 0;
          overflow-wrap: anywhere;
        }

        .pagination-or-search {
          align-items: center;
          display: flex;
          flex-wrap: wrap;
          gap: 0.75rem;
          min-width: 0;

          @media ($mobile-site) {
            display: flex;
            min-height: 36px;
            align-items: center;
          }

          .form-group {
            margin: 0;
            max-width: 100%;
            min-width: 0;
            width: 100%;

            input {
              min-width: 0;
            }
          }
        }
      }

      .actions {
        display: flex;
        flex-shrink: 0;

        @media ($mobile-site) {
          flex-direction: column;

          .add-button {
            order: 1;
          }

          .search-button {
            order: 2;
            margin-top: 5px;
          }
        }

        .search-button {
          @media ($desktop-site) {
            margin: 0 5px;
          }

          .search-symbol {
            -webkit-transform: rotate(45deg);
            -moz-transform: rotate(45deg);
            -o-transform: rotate(45deg);
            transform: rotate(45deg);
            font-size: 1em;
          }
        }
      }
    }

    .no-records {
      margin-top: 20px;
    }
  }

  .resource-filter-bar {
    align-items: flex-end;
    box-sizing: border-box;
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin: 1rem 0;
    max-width: 100%;
    min-width: 0;
    width: 100%;

    .resource-filter-select {
      box-sizing: border-box;
      flex: 1 1 230px;
      margin: 0;
      max-width: 100%;
      min-width: min(230px, 100%);

      label {
        color: $color-text-hex-60;
        font-size: 13px;
        letter-spacing: 0.5px;
        line-height: 16px;
        padding-bottom: 0.35rem;
      }

      select {
        max-width: 100%;
        min-width: 0;
      }
    }

    @media ($mobile-site) {
      align-items: stretch;
      flex-direction: column;

      .resource-filter-select {
        flex-basis: auto;
        min-width: 0;
        width: 100%;
      }
    }
  }

  .resource-filters {
    margin-left: 20px;
    //@media ($mobile-site) { margin-left: 10px; }
    flex-grow: 0;
    flex-shrink: 0;
  }
}
</style>
