<template>
  <div class="room-triggers">
    <h2>ROOM TRIGGERS</h2>
    <div class="color-text-60 mb-6">
      Triggers are authored command handlers scoped to this room. Edit them via YAML in World Edit.
    </div>

    <div v-if="newTriggerTemplate" class="trigger-card template-card">
      <div class="trigger-header">
        <div>
          <div class="trigger-name">New Room Trigger Template</div>
          <div class="trigger-meta color-text-60">Copy this YAML, tweak it, then ingest it in Edit World.</div>
        </div>
        <div class="template-actions">
          <button class="btn-small template-copy-action" @click="copyTemplateYaml">COPY YAML</button>
          <button class="btn-thin template-toggle" @click="toggleTemplateExpanded">
            {{ isTemplateExpanded ? "HIDE YAML" : "SHOW YAML" }}
          </button>
        </div>
      </div>

      <pre v-if="isTemplateExpanded" class="trigger-yaml"><code>{{ newTriggerTemplate.yaml }}</code></pre>
    </div>

    <div v-if="isLoading" class="color-text-60">Loading triggers...</div>
    <div v-else-if="triggers.length === 0" class="color-text-60">
      No room-scoped triggers found for this room.
    </div>

    <div v-for="trigger in triggers" :key="trigger.id" class="trigger-card">
      <div class="trigger-header">
        <div>
          <div class="trigger-name">{{ trigger.name || trigger.key }}</div>
          <div class="trigger-meta color-text-60">{{ trigger.scope }} / {{ trigger.kind }}</div>
        </div>
        <div class="trigger-actions">
          <button class="btn-thin" @click="copyYaml(trigger)">COPY YAML</button>
          <button class="btn-thin ml-2" @click="copyDeleteYaml(trigger)">COPY DELETE YAML</button>
        </div>
      </div>

      <pre class="trigger-yaml"><code>{{ trigger.yaml }}</code></pre>
    </div>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";

const route = useRoute();
const store = useStore();

const isLoading = ref(false);
const triggers = ref<any[]>([]);
const newTriggerTemplate = ref<any | null>(null);
const isTemplateExpanded = ref(false);

const endpoint = `/builder/worlds/${route.params.world_id}/rooms/${route.params.room_id}/triggers/`;

const fetchTriggers = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint);
    newTriggerTemplate.value = resp.data.new_trigger_template || null;
    isTemplateExpanded.value = false;
    triggers.value = resp.data.triggers || [];
  } finally {
    isLoading.value = false;
  }
};

const copyYaml = async (trigger: any) => {
  try {
    await navigator.clipboard.writeText(trigger.yaml || "");
    store.commit("ui/notification_set", "Trigger YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
  }
};

const copyTemplateYaml = async () => {
  if (!newTriggerTemplate.value) return;
  await copyYaml(newTriggerTemplate.value);
};

const toggleTemplateExpanded = () => {
  isTemplateExpanded.value = !isTemplateExpanded.value;
};

const copyDeleteYaml = async (trigger: any) => {
  try {
    await navigator.clipboard.writeText(trigger.delete_yaml || "");
    store.commit("ui/notification_set", "Trigger delete YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy delete YAML to clipboard.");
  }
};

onMounted(async () => {
  await fetchTriggers();
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.room-triggers {
  .trigger-card {
    border: 1px solid $color-form-border;
    margin-bottom: 1.5rem;
    padding: 0.75rem;
    background: $color-background-light-border;
  }

  .template-card {
    margin-bottom: 2rem;
  }

  .trigger-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }

  .trigger-name {
    font-weight: 600;
  }

  .trigger-meta {
    font-size: 0.95rem;
  }

  .template-actions {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .template-copy-action {
    white-space: nowrap;
  }

  .template-toggle {
    white-space: nowrap;
  }

  .trigger-yaml {
    margin: 0;
    padding: 0.75rem;
    overflow-x: auto;
    border: 0;
    background: $color-background;
    white-space: pre-wrap;
    word-break: break-word;

    code {
      border: 0;
      padding: 0;
      display: block;
      word-spacing: normal;
      background: transparent;
    }
  }
}
</style>
