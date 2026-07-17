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
              v-else-if="totalPages > 1"
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
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
let searchTimeout: ReturnType<typeof setTimeout> | null = null;
let filterTimeout: ReturnType<typeof setTimeout> | null = null;

const elements = computed(() => results.value.map((element) => ({
  ...element,
  link: router.resolve(props.resolveRoute(element)).href,
})));

const totalPages = computed(() => Math.max(1, Math.ceil(resultCount.value / 10)));

const fetchData = async () => {
  const activeRequest = ++requestNumber;
  loading.value = true;
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
    results.value = [];
    resultCount.value = 0;
    errorMessage.value = craftingApiErrorMessage(
      error,
      `Could not load ${props.title.toLowerCase()}.`,
    );
  } finally {
    if (activeRequest === requestNumber) loading.value = false;
  }
};

const onSetPage = (nextPage: number) => {
  pageNum.value = nextPage;
  fetchData();
};

const onSort = (sortKey: string) => {
  if (!sortKey) return;
  const descendingKey = `-${sortKey}`;
  if (sortBy.value === sortKey) {
    sortBy.value = descendingKey;
  } else {
    sortBy.value = sortKey;
  }
  pageNum.value = 1;
  fetchData();
};

const onFilterInput = (attr: string, event: Event) => {
  const target = event.target as HTMLInputElement | null;
  filterValues.value = {
    ...filterValues.value,
    [attr]: target?.value || "",
  };
};

const toggleSearch = async () => {
  showSearch.value = !showSearch.value;
  if (!showSearch.value) {
    searchText.value = "";
    return;
  }
  await nextTick();
  searchInput.value?.focus();
};

watch(searchText, () => {
  if (searchTimeout) clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    pageNum.value = 1;
    fetchData();
  }, 250);
});

watch(filterValues, () => {
  if (filterTimeout) clearTimeout(filterTimeout);
  filterTimeout = setTimeout(() => {
    pageNum.value = 1;
    fetchData();
  }, 250);
});

watch(() => props.endpoint, () => {
  pageNum.value = 1;
  fetchData();
});

onMounted(fetchData);

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
  min-width: 0;

  .form-group {
    margin: 0;
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

  .search-button {
    margin: 5px 0 0;
    order: 2;
  }

  .add-button {
    order: 1;
  }
}
</style>
