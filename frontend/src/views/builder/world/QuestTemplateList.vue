<template>
  <div id="quest-template-list">
    <h2>QUEST TEMPLATES</h2>
    <div class="color-text-60 mb-6">
      Author the new world-scoped quest system here. Each quest is stored as a YAML manifest and applied through the builder manifest ingestion pipeline.
    </div>

    <div class="quest-toolbar mb-6">
      <input
        v-model="query"
        class="quest-search"
        type="text"
        placeholder="Search by id, name, or slug"
        @keyup.enter="fetchQuests"
      />
      <button class="btn-small" :disabled="isLoading" @click="fetchQuests">SEARCH</button>
      <button class="btn-small" :disabled="isLoading" @click="clearSearch">CLEAR</button>
    </div>

    <div v-if="newQuestTemplate" class="quest-card template-card">
      <div class="quest-card-header">
        <div>
          <div class="quest-name">New Quest Template</div>
          <div class="quest-meta color-text-60">
            Start from the generated manifest template, then apply it to create a world-scoped quest.
          </div>
        </div>
        <div class="quest-actions">
          <router-link class="btn-thin" :to="newQuestRoute">VIEW</router-link>
        </div>
      </div>
    </div>

    <div v-if="isLoading" class="color-text-60">Loading quest templates...</div>
    <div v-else-if="quests.length === 0" class="color-text-60">
      No quest templates found for this world.
    </div>

    <div v-for="quest in quests" :key="quest.id" class="quest-card">
      <div class="quest-card-header">
        <div>
          <router-link class="quest-name" :to="questRoute(quest)">
            {{ quest.name }}
          </router-link>
          <div class="quest-meta color-text-60">
            {{ quest.slug }} / {{ quest.quest_type }} / {{ quest.status }} / {{ quest.scope }}
          </div>
          <div class="quest-summary color-text-60">
            {{ summarizeQuest(quest) }}
          </div>
        </div>
        <div class="quest-actions">
          <router-link class="btn-thin" :to="questRoute(quest)">VIEW</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";

const route = useRoute();
const store = useStore();

const endpoint = computed(() => `/builder/worlds/${route.params.world_id}/questtemplates/`);
const newQuestRoute = computed(() => ({
  name: "builder_world_quest_template_new",
  params: {
    world_id: route.params.world_id,
  },
}));

const isLoading = ref(false);
const query = ref("");
const quests = ref<any[]>([]);
const newQuestTemplate = ref<any | null>(null);

const extractError = (error: any): string => {
  const data = error?.response?.data;
  if (!data) return "Could not load quest templates.";
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
  return "Could not load quest templates.";
};

const fetchQuests = async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(endpoint.value, {
      params: query.value.trim() ? { query: query.value.trim() } : {},
    });
    quests.value = resp.data.quests || [];
    newQuestTemplate.value = resp.data.new_quest_template || null;
  } catch (error: any) {
    quests.value = [];
    newQuestTemplate.value = null;
    store.commit("ui/notification_set_error", extractError(error));
  } finally {
    isLoading.value = false;
  }
};

const clearSearch = async () => {
  query.value = "";
  await fetchQuests();
};

const questRoute = (quest: any) => ({
  name: "builder_world_quest_template_details",
  params: {
    world_id: route.params.world_id,
    quest_template_id: quest.slug || quest.id,
  },
});

const summarizeQuest = (quest: any): string => {
  const stepCount = Array.isArray(quest.graph?.steps) ? quest.graph.steps.length : 0;
  const sourceTypes = Array.isArray(quest.discovery_policy?.sources)
    ? quest.discovery_policy.sources.map((source: any) => source.type).filter(Boolean)
    : [];
  const summaryParts = [`${stepCount} step${stepCount === 1 ? "" : "s"}`];
  if (sourceTypes.length) {
    summaryParts.push(`sources: ${sourceTypes.join(", ")}`);
  } else {
    summaryParts.push("no discovery sources configured");
  }
  if (quest.arc?.name) {
    summaryParts.push(`arc: ${quest.arc.name}`);
  }
  return summaryParts.join(" • ");
};

onMounted(async () => {
  await fetchQuests();
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#quest-template-list {
  .quest-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
  }

  .quest-search {
    min-width: 280px;
    flex: 1;
    padding: 0.55rem 0.75rem;
    border: 1px solid $color-form-border;
    background: $color-background;
    color: $color-text;
  }

  .quest-card {
    border: 1px solid $color-form-border;
    margin-bottom: 1.5rem;
    padding: 0.9rem;
    background: $color-background-light-border;
  }

  .template-card {
    margin-bottom: 2rem;
  }

  .quest-card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 0.75rem;
  }

  .quest-name {
    font-weight: 600;
    text-decoration: none;
  }

  .quest-meta,
  .quest-summary {
    font-size: 0.95rem;
  }

  .quest-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
  }

  .quest-yaml {
    margin: 1rem 0 0 0;
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
      background: transparent;
    }
  }
}
</style>
