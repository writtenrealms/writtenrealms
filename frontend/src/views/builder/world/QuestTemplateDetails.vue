<template>
  <div id="quest-template-details">
    <div v-if="isLoading" class="color-text-60">Loading quest editor...</div>
    <template v-else>

      <QuestTemplateCard v-if="quest" :quest="quest" class="mb-6" />

      <div class="editor-actions mb-4">
        <button class="btn-small" :disabled="isSubmitting || !manifestText.trim()" @click="submitManifest">
          APPLY QUEST
        </button>
        <button class="btn-thin ml-2" :disabled="isSubmitting || manifestText === loadedYaml" @click="resetManifest">
          RESET
        </button>
        <button class="btn-thin ml-2" :disabled="!manifestText.trim()" @click="copyCurrentYaml">
          COPY YAML
        </button>
        <button v-if="quest" class="btn-thin ml-2" @click="copyDeleteYaml">
          COPY DELETE YAML
        </button>
        <button v-if="quest" class="btn-thin ml-2" :disabled="isSubmitting" @click="deleteQuest">
          DELETE QUEST
        </button>
      </div>

      <textarea
        v-model="manifestText"
        class="manifest-input"
        placeholder="Paste or edit quest YAML here..."
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
import QuestTemplateCard from "@/components/builder/world/QuestTemplateCard.vue";

const route = useRoute();
const router = useRouter();
const store = useStore();

const listEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/questtemplates/`);
const manifestApplyEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/manifests/apply/`);
const detailEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/questtemplates/${route.params.quest_template_id}/`);
const isCreateMode = computed(() => route.name === "builder_world_quest_template_new");

const isLoading = ref(false);
const isSubmitting = ref(false);
const quest = ref<any | null>(null);
const manifestText = ref("");
const loadedYaml = ref("");

const title = computed(() => {
  if (quest.value?.name) {
    return quest.value.name.toUpperCase();
  }
  return "NEW QUEST";
});

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not apply quest manifest.";
  if (typeof data === "string") return data;
  if (Array.isArray(data)) {
    const first = data[0];
    return typeof first === "string" ? first : JSON.stringify(first);
  }
  if (typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    const firstKey = Object.keys(data)[0];
    const value = data[firstKey];
    if (Array.isArray(value)) {
      const first = value[0];
      return typeof first === "string" ? first : JSON.stringify(first);
    }
    if (typeof value === "string") return value;
    return JSON.stringify(value);
  }
  return "Could not apply quest manifest.";
};

const setLoadedState = (payload: any) => {
  quest.value = payload;
  loadedYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
};

const loadQuest = async () => {
  isLoading.value = true;
  try {
    if (isCreateMode.value) {
      const resp = await axios.get(listEndpoint.value);
      quest.value = null;
      loadedYaml.value = resp.data.new_quest_template?.yaml || "";
      manifestText.value = resp.data.new_quest_template?.yaml || "";
    } else {
      const resp = await axios.get(detailEndpoint.value);
      setLoadedState(resp.data);
    }
  } catch (error: any) {
    quest.value = null;
    loadedYaml.value = "";
    manifestText.value = "";
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const copyText = async (value: string, successMessage: string) => {
  try {
    await navigator.clipboard.writeText(value || "");
    store.commit("ui/notification_set", successMessage);
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
  }
};

const copyCurrentYaml = async () => {
  await copyText(manifestText.value, "Quest YAML copied.");
};

const copyDeleteYaml = async () => {
  if (!quest.value) return;
  await copyText(quest.value.delete_yaml || "", "Quest delete YAML copied.");
};

const resetManifest = () => {
  manifestText.value = loadedYaml.value;
};

const syncRouteToQuest = async (payload: any) => {
  const slug = payload?.slug;
  if (!slug) return;
  if (route.name === "builder_world_quest_template_details" && route.params.quest_template_id === slug) {
    return;
  }
  await router.replace({
    name: "builder_world_quest_template_details",
    params: {
      world_id: route.params.world_id,
      quest_template_id: slug,
    },
  });
};

const submitManifest = async () => {
  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: manifestText.value,
    });
    if (resp.data.kind !== "quest") {
      throw new Error("Unexpected manifest response kind.");
    }
    const appliedQuest = resp.data.quest || null;
    if (appliedQuest) {
      setLoadedState(appliedQuest);
      await syncRouteToQuest(appliedQuest);
    }
    store.commit("ui/notification_set", `Quest ${resp.data.operation}.`);
  } catch (error: any) {
    if (error?.message === "Unexpected manifest response kind.") {
      store.commit("ui/notification_set_error", "Manifest apply did not return a quest payload.");
    } else {
      store.commit("ui/notification_set_error", extractError(error));
    }
  } finally {
    isSubmitting.value = false;
  }
};

const deleteQuest = async () => {
  if (!quest.value?.delete_yaml) return;
  if (!window.confirm(`Delete quest '${quest.value.name}'?`)) return;

  isSubmitting.value = true;
  try {
    const resp = await axios.post(manifestApplyEndpoint.value, {
      manifest: quest.value.delete_yaml,
    });
    store.commit("ui/notification_set", `Quest ${resp.data.operation}.`);
    await router.push({
      name: "builder_world_quest_template_list",
      params: {
        world_id: route.params.world_id,
      },
    });
  } catch (error: any) {
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(async () => {
  await loadQuest();
});

watch(
  () => route.params.quest_template_id,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await loadQuest();
  },
);

watch(
  () => route.name,
  async (nextValue, prevValue) => {
    if (nextValue === prevValue) return;
    await loadQuest();
  },
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#quest-template-details {
  .editor-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
  }

  .manifest-input {
    width: 100%;
    min-height: 560px;
    padding: 0.75rem;
    border: 1px solid $color-form-border;
    background: $color-background;
    color: $color-text;
    font-family: monospace;
    line-height: 1.35;
  }
}
</style>
