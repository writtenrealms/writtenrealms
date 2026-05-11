<template>
  <span v-if="isEmptyValue(value)" class="manifest-empty">(empty)</span>

  <span v-else-if="isPrimitiveValue(value)" class="manifest-primitive">
    {{ primitiveLabel(value) }}
  </span>

  <div v-else-if="isPrimitiveArray(value)" class="manifest-token-list">
    <span
      v-for="(item, index) in value"
      :key="index"
      class="manifest-token"
    >{{ primitiveLabel(item) }}</span>
  </div>

  <div v-else-if="recordTable" class="data-table-scroll manifest-table-scroll">
    <table class="data-table record-table manifest-record-table">
      <thead>
        <tr>
          <th>{{ recordTable.rowHeader }}</th>
          <th v-for="column in recordTable.columns" :key="column">
            {{ labelForKey(column) }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in recordTable.rows" :key="row.key">
          <th scope="row">{{ row.label }}</th>
          <td v-for="column in recordTable.columns" :key="column">
            <ManifestValue :value="row.data[column]" :depth="depth + 1" :collapse-complex="collapseComplex" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <div v-else-if="keyValueTable" class="data-table-scroll manifest-table-scroll">
    <table class="data-table key-value-table manifest-key-value-table">
      <tbody>
        <tr v-for="row in keyValueTable" :key="row.key">
          <th scope="row">{{ row.label }}</th>
          <td>
            <ManifestValue :value="row.value" :depth="depth + 1" :collapse-complex="collapseComplex" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <ol v-else-if="Array.isArray(value)" class="manifest-list">
    <li v-for="(item, index) in value" :key="index">
      <ManifestValue :value="item" :depth="depth + 1" :collapse-complex="collapseComplex" />
    </li>
  </ol>

  <div v-else class="manifest-map" :class="{ nested: depth > 0, root: depth === 0 }">
    <div
      v-for="[key, item] in objectEntries"
      :key="key"
      class="manifest-map-row"
      :class="{ full: isWideMapValue(item) }"
    >
      <details
        v-if="collapseComplex && isLargeComplexValue(item) && !isRecordTableValue(item)"
        class="manifest-details"
      >
        <summary>
          <span>{{ labelForKey(key) }}</span>
          <span class="manifest-summary">{{ valueSummary(item) }}</span>
        </summary>
        <ManifestValue :value="item" :depth="depth + 1" :collapse-complex="collapseComplex" />
      </details>
      <template v-else>
        <div class="manifest-key">{{ labelForKey(key) }}</div>
        <div class="manifest-map-value">
          <ManifestValue :value="item" :depth="depth + 1" :collapse-complex="collapseComplex" />
        </div>
      </template>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";

defineOptions({ name: "ManifestValue" });

const props = withDefaults(defineProps<{
  value: any;
  depth?: number;
  collapseComplex?: boolean;
}>(), {
  depth: 0,
  collapseComplex: false,
});

const isPlainObject = (value: any) => {
  return value !== null && typeof value === "object" && !Array.isArray(value);
};

const isEmptyValue = (value: any) => {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (isPlainObject(value)) return Object.keys(value).length === 0;
  return false;
};

const isPrimitiveValue = (value: any) => {
  return ["string", "number", "boolean"].includes(typeof value);
};

const isPrimitiveArray = (value: any) => {
  return Array.isArray(value) && value.every((item) => isEmptyValue(item) || isPrimitiveValue(item));
};

const isCompactPrimitive = (value: any) => {
  if (isEmptyValue(value) || typeof value === "number" || typeof value === "boolean") return true;
  if (typeof value !== "string") return false;
  return value.length <= 120 && !value.includes("\n");
};

const isSimpleTableValue = (value: any) => {
  if (isCompactPrimitive(value)) return true;
  return isPrimitiveArray(value) && value.every((item) => isCompactPrimitive(item));
};

const columnsForRows = (rows: any[]) => {
  const columns: string[] = [];
  rows.forEach((row) => {
    Object.keys(row).forEach((column) => {
      if (!columns.includes(column)) columns.push(column);
    });
  });
  return columns;
};

const canRenderRecordRows = (rows: any[]) => {
  if (rows.length < 2) return false;
  if (!rows.every((row) => isPlainObject(row) && Object.keys(row).length)) return false;
  if (!rows.every((row) => Object.values(row).every((item) => isSimpleTableValue(item)))) return false;
  return columnsForRows(rows).length <= 8;
};

const recordRowsForValue = (value: any) => {
  if (Array.isArray(value)) return value;
  if (!isPlainObject(value)) return [];
  return Object.values(value);
};

const isRecordTableValue = (value: any) => {
  return canRenderRecordRows(recordRowsForValue(value));
};

const isWideMapValue = (value: any) => {
  if (isRecordTableValue(value)) return true;
  if (Array.isArray(value)) return !isPrimitiveArray(value);
  if (!isPlainObject(value)) return false;
  if (props.depth === 0) return true;
  return !Object.values(value).every((item) => isSimpleTableValue(item));
};

const hasNestedComplexValue = (value: any) => {
  if (!isPlainObject(value)) return false;
  return Object.values(value).some((item) => Array.isArray(item) || isPlainObject(item));
};

const isLargeComplexValue = (value: any) => {
  if (Array.isArray(value)) {
    return value.length > 6 || value.some((item) => isPlainObject(item) || Array.isArray(item));
  }
  if (!isPlainObject(value)) return false;
  return Object.keys(value).length > 3 || hasNestedComplexValue(value);
};

const valueSummary = (value: any) => {
  if (Array.isArray(value)) {
    const suffix = value.length === 1 ? "entry" : "entries";
    return `${value.length} ${suffix}`;
  }
  if (isPlainObject(value)) {
    const count = Object.keys(value).length;
    const suffix = count === 1 ? "field" : "fields";
    return `${count} ${suffix}`;
  }
  return "";
};

const primitiveLabel = (value: any) => {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (isEmptyValue(value)) return "(empty)";
  return String(value);
};

const labelForKey = (key: string) => {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const objectEntries = computed(() => {
  if (!isPlainObject(props.value)) return [];
  return Object.entries(props.value);
});

const recordTable = computed(() => {
  if (Array.isArray(props.value)) {
    if (!canRenderRecordRows(props.value)) return null;
    return {
      rowHeader: "#",
      columns: columnsForRows(props.value),
      rows: props.value.map((row, index) => ({
        key: String(index),
        label: String(index + 1),
        data: row,
      })),
    };
  }

  const entries = objectEntries.value;
  const rows = entries.map(([, item]) => item);
  if (!canRenderRecordRows(rows)) return null;

  return {
    rowHeader: "Name",
    columns: columnsForRows(rows),
    rows: entries.map(([key, item]) => ({
      key,
      label: labelForKey(key),
      data: item,
    })),
  };
});

const keyValueTable = computed(() => {
  const entries = objectEntries.value;
  if (!entries.length) return null;
  if (!entries.every(([, item]) => isSimpleTableValue(item))) return null;
  return entries.map(([key, value]) => ({
    key,
    label: labelForKey(key),
    value,
  }));
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.manifest-empty {
  color: $color-text-hex-50;
}

.manifest-primitive {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.manifest-token-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.manifest-token {
  border: 1px solid $color-background-light-border;
  background: $color-background-light;
  padding: 0.2rem 0.45rem;
  color: $color-text-hex-80;
}

.manifest-list {
  margin: 0;
  padding-left: 1.25rem;

  li {
    margin-bottom: 0.75rem;
  }
}

.manifest-map {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: 1fr;
  max-width: 100%;
  min-width: 0;

  &.nested {
    border-left: 1px solid $color-background-light-border;
    padding-left: 1rem;
  }

  &.root {
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  }
}

.manifest-map-row {
  align-content: start;
  display: grid;
  gap: 0.35rem 0.85rem;
  grid-template-columns: minmax(8rem, 0.35fr) minmax(0, 1fr);
  min-width: 0;

  &.full {
    grid-column: 1 / -1;
    grid-template-columns: 1fr;
  }

  @media (max-width: 680px) {
    grid-template-columns: 1fr;
  }
}

.manifest-key {
  color: $color-secondary;
  font-size: 0.85rem;
}

.manifest-map-value {
  color: $color-text;
  max-width: 100%;
  min-width: 0;
}

.manifest-details {
  max-width: 100%;
  min-width: 0;

  summary {
    color: $color-secondary;
    cursor: pointer;
    display: flex;
    gap: 0.5rem;
    justify-content: space-between;
  }

  > .manifest-map,
  > .manifest-table-scroll,
  > .manifest-list {
    margin-top: 0.75rem;
  }
}

.manifest-summary {
  color: $color-text-hex-60;
}
</style>
