<template>
  <div class="element-table-wrapper">
    <div class="table-responsive" :class="{ 'data-table-scroll': variant === 'data' }">
      <table :class="tableClasses">
        <thead>
          <tr>
            <th
              v-for="field in schema"
              :key="field.name"
              :class="{ 'mobile-hidden-column': field.mobileHidden }"
              :aria-sort="getAriaSort(field)"
            >
              <button
                v-if="getSortKey(field)"
                class="table-sort-button"
                :class="{
                  active: isSorted(field),
                  descending: isSortedDescending(field)
                }"
                type="button"
                @click="onSort(field)"
              >
                <span>{{ field.label }}</span>
                <span class="sort-indicator" aria-hidden="true"></span>
              </button>
              <template v-else>{{ field.label }}</template>
            </th>
          </tr>
        </thead>

        <tbody>
          <tr v-for="element in elements" :key="element.id">
            <td
              v-for="field in schema"
              :key="field.name"
              :class="{ 'mobile-hidden-column': field.mobileHidden }"
              :nowrap="isNowrap(field)"
            >
              <a :href="element.link" class="link-full-cell">
                {{ getFieldValue(element, field.name) || "&nbsp;" }}
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { ElementListSchema } from "@/core/interfaces.ts";

const props = withDefaults(defineProps<{
  title: string;
  schema: any;
  elements: Array<ElementListSchema>;
  variant?: "default" | "data";
  sortBy?: string;
}>(), {
  variant: "default",
  sortBy: "",
});
const emit = defineEmits(["sort"]);

const tableClasses = computed(() => {
  if (props.variant === "data") {
    return ["data-table", "record-table", "element-table"];
  }
  return ["table"];
});

const isNowrap = (field: any) => {
  return field.nowrap;
};

const getSortKey = (field: any) => {
  if (field.sortKey) {
    return field.sortKey;
  }
  if (field.sortable) {
    return field.name;
  }
  return "";
};

const isSorted = (field: any) => {
  const sortKey = getSortKey(field);
  return props.sortBy === sortKey || props.sortBy === `-${sortKey}`;
};

const isSortedDescending = (field: any) => {
  const sortKey = getSortKey(field);
  return props.sortBy === `-${sortKey}`;
};

const getAriaSort = (field: any) => {
  if (!getSortKey(field)) {
    return undefined;
  }
  if (!isSorted(field)) {
    return "none";
  }
  return isSortedDescending(field) ? "descending" : "ascending";
};

const onSort = (field: any) => {
  emit("sort", getSortKey(field));
};

const getRawFieldValue = (element: ElementListSchema, name: string) => {
  if (name.indexOf(".") !== -1) {
    const parts = name.split(".");
    const parent = element[parts[0]];
    return parent[parts[1]];
  }
  return element[name];
};

const getFieldValue = (element: ElementListSchema, name: string) => {
  const field = props.schema.find((field: any) => field.name === name);
  const value = getRawFieldValue(element, name);
  if (field?.format) {
    return field.format(value, element);
  }
  return value;
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.element-table-wrapper,
.table-responsive {
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
  width: 100%;
}

thead {
  .table-sort-button {
    align-items: center;
    background: transparent;
    border: 0;
    color: inherit;
    display: inline-flex;
    font: inherit;
    gap: 0.35rem;
    padding: 0;
    text-align: left;

    &:hover,
    &:focus,
    &.active {
      color: $color-secondary;
    }
  }

  .sort-indicator {
    border-color: currentColor;
    border-style: solid;
    border-width: 0 1px 1px 0;
    display: inline-block;
    flex-shrink: 0;
    height: 0.35rem;
    margin-top: -0.15rem;
    opacity: 0;
    transform: rotate(45deg);
    width: 0.35rem;
  }

  .table-sort-button:hover .sort-indicator,
  .table-sort-button:focus .sort-indicator,
  .table-sort-button.active .sort-indicator {
    opacity: 0.65;
  }

  .table-sort-button.descending .sort-indicator {
    margin-top: 0.15rem;
    transform: rotate(225deg);
  }
}

tbody {
  tr {
    td {
      position: relative;
      padding: 0;

      a.link-full-cell {
        display: block;
        width: 100%;
        height: 100%;
        color: inherit;
        text-decoration: none;
        padding: 16px 8px;
        box-sizing: border-box;

        &:hover,
        &:active {
          text-decoration: none;
        }
      }
    }
  }
}

.data-table-scroll {
  tbody {
    tr {
      td {
        a.link-full-cell {
          padding: 0.45rem 0.75rem;
        }
      }
    }
  }
}

@media ($mobile-site) {
  .mobile-hidden-column {
    display: none;
  }
}
</style>
