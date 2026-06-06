<template>
  <div id="trigger-details">
    <div v-if="isLoading" class="color-text-60">Loading trigger...</div>

    <template v-else-if="trigger">
      <div class="trigger-header mb-4">
        <div>
          <h2>{{ trigger.name || trigger.key }}</h2>
          <div class="color-text-60">
            ID: {{ trigger.id }} | Key: {{ trigger.key }} | {{ trigger.scope }} / {{ trigger.kind }}
          </div>
        </div>

        <div class="trigger-actions">
          <button class="btn-small" :disabled="!trigger.yaml" @click="copyYaml">
            COPY YAML
          </button>
          <button class="btn-small" :disabled="!trigger.yaml" @click="editYaml">
            EDIT
          </button>
          <button class="btn-thin" :disabled="!trigger.delete_yaml" @click="copyDeleteYaml">
            COPY DELETE YAML
          </button>
        </div>
      </div>

      <section class="trigger-summary mb-4">
        <div class="summary-row">
          <div class="summary-label">Status</div>
          <div>{{ trigger.is_active ? "Active" : "Inactive" }}</div>
        </div>
        <div class="summary-row">
          <div class="summary-label">Target</div>
          <ManifestValue :value="trigger.target || {}" />
        </div>
        <div class="summary-row" v-if="trigger.match">
          <div class="summary-label">Match</div>
          <ManifestValue :value="trigger.match" />
        </div>
        <div class="summary-row" v-if="trigger.event">
          <div class="summary-label">Event</div>
          <ManifestValue :value="trigger.event" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Gate Delay</div>
          <ManifestValue :value="trigger.gate_delay" />
        </div>
        <div class="summary-row">
          <div class="summary-label">Order</div>
          <ManifestValue :value="trigger.order" />
        </div>
        <div class="summary-row full">
          <div class="summary-label">Conditions</div>
          <ManifestValue :value="trigger.conditions" :collapse-complex="true" />
        </div>
        <div class="summary-row full">
          <div class="summary-label">Script</div>
          <ManifestValue :value="trigger.script" />
        </div>
      </section>

      <textarea
        :value="trigger.yaml || ''"
        class="manifest-output"
        readonly
        spellcheck="false"
      />
    </template>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import ManifestValue from "@/components/builder/world/ManifestValue.vue";

const route = useRoute();
const router = useRouter();
const store = useStore();

const trigger = ref<any | null>(null);
const isLoading = ref(false);
const endpoint = computed(() => (
  route.params.room_id
    ? `/builder/worlds/${route.params.world_id}/rooms/${route.params.room_id}/triggers/${route.params.trigger_id}/`
    : `/builder/worlds/${route.params.world_id}/triggers/${route.params.trigger_id}/`
));

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not load trigger.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data[0] || "Could not load trigger.";
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) return value[0];
    if (typeof value === "string") return value;
  }
  return "Could not load trigger.";
};

const fetchTrigger = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value);
    trigger.value = resp.data;
  } catch (error: any) {
    trigger.value = null;
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyYaml = async () => {
  try {
    await navigator.clipboard.writeText(trigger.value?.yaml || "");
    store.commit("ui/notification_set", "Trigger YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
  }
};

const copyDeleteYaml = async () => {
  try {
    await navigator.clipboard.writeText(trigger.value?.delete_yaml || "");
    store.commit("ui/notification_set", "Trigger delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy delete YAML to clipboard.");
  }
};

const editYaml = () => {
  if (!trigger.value) return;
  router.push({
    name: "builder_world_edit",
    params: {
      world_id: route.params.world_id,
    },
    query: {
      prefill: "trigger",
      trigger_id: trigger.value.id,
      ...(route.params.room_id ? { room_id: String(route.params.room_id) } : {}),
    },
  });
};

onMounted(fetchTrigger);

watch(
  () => route.params.trigger_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await fetchTrigger();
  },
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

#trigger-details {
  box-sizing: border-box;
  min-width: 0;
  width: 100%;

  .trigger-header {
    align-items: flex-start;
    display: flex;
    gap: 1rem;
    justify-content: space-between;

    @media ($mobile-site) {
      flex-direction: column;
    }
  }

  .trigger-actions {
    display: flex;
    flex-shrink: 0;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .trigger-summary {
    border-top: 1px solid $color-background-light-border;
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    max-width: 960px;
    padding-top: 1rem;
  }

  .summary-row {
    min-width: 0;

    &.full {
      grid-column: 1 / -1;
    }
  }

  .summary-label {
    color: $color-text-hex-60;
    margin-bottom: 0.35rem;
  }

  .manifest-output {
    box-sizing: border-box;
    width: 100%;
    min-height: 520px;
    padding: 0.75rem;
    border: 1px solid $color-form-border;
    background: $color-background;
    color: $color-text;
    font-family: monospace;
    line-height: 1.35;
  }
}
</style>
