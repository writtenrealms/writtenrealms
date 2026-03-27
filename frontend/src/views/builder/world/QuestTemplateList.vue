<template>
  <div id="quest-template-list">
    <h2>QUEST TEMPLATES</h2>

    <div class="new-quest my-6">
      <router-link :to="newQuestRoute">
        <button class="btn-small">NEW QUEST TEMPLATE</button>
      </router-link>
    </div>

    <div class="quest-toolbar my-6">
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



    <div v-if="isLoading" class="color-text-60">Loading quest templates...</div>
    <div v-else-if="quests.length === 0" class="color-text-60">
      No quest templates found for this world.
    </div>

    <QuestTemplateCard
      v-for="quest in quests"
      :key="quest.id"
      class="mb-6"
      :quest="quest"
      :to="questRoute(quest)"
    />
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import QuestTemplateCard from "@/components/builder/world/QuestTemplateCard.vue";

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
  } catch (error: any) {
    quests.value = [];
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

}
</style>
