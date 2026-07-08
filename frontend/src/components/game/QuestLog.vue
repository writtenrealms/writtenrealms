<template>
  <div id="quest_log" v-if="fetched">
    <div class="close-button hover" aria-label="Close" @click="closeQuestLog">
      <span aria-hidden="true">&#10006;</span>
    </div>

    <div class="my-4">
      <h1 class="mb-4">Quest Log</h1>

      <div class="tabs-view">
        <div class="tabs">
          <div
            class="tab-item"
            :class="{ activeTab: selectedTab == 'active' }"
            @click="clickTab('active')">Active Quests</div>
          <div
            class="tab-item"
            :class="{ activeTab: selectedTab == 'resolved' }"
            @click="clickTab('resolved')">Resolved Quests</div>
        </div>
      </div>

      <template v-if="quests.length">
        <div v-for="quest in quests" :key="quest.id" class="quest-entry mt-4">
          <h2 @click="onClickName(quest)" class="mb-2">
            <span class="interactive">{{ quest.template.name }}</span>
            <span class="ml-2 color-text-50" v-if="store.state.game.player.is_builder">[ {{ quest.id }} ]</span>
          </h2>
          <template v-if="expanded == quest.id">
            <div class="quest-meta color-text-50">
              {{ quest.status }}
              <template v-if="quest.resolution"> - {{ quest.resolution }}</template>
              <template v-if="quest.template.slug"> - {{ quest.template.slug }}</template>
            </div>

            <div v-if="quest.current_step.recap" class="my-2">
              {{ quest.current_step.recap }}
            </div>
            <div v-if="quest.current_step.text.body" class="my-2">
              {{ quest.current_step.text.body }}
            </div>

            <div v-if="visibleObjectives(quest).length" class="quest-objectives my-2">
              <div class="quest-section-label">Objectives</div>
              <div
                v-for="objective in visibleObjectives(quest)"
                :key="objective.id"
                class="quest-objective"
              >
                <span>{{ objective.text || objective.id }}</span>
                <span class="quest-progress color-text-50">{{ objectiveProgress(objective) }}</span>
              </div>
            </div>

            <div v-if="quest.latest_journal_entry?.recap" class="my-2 color-text-50">
              Last change: {{ quest.latest_journal_entry.recap }}
            </div>

            <button
              v-if="quest.status == 'active'"
              class="btn-small mt-2"
              @click="showQuestInfo(quest)"
            >
              INFO
            </button>
          </template>
        </div>
      </template>
      <template v-else>
        <div class="mt-6" v-if="selectedTab == 'resolved'">No resolved quests.</div>
        <div class="mt-6" v-else>No active quests.</div>
      </template>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted } from "vue";
import { useStore } from "vuex";
import axios from "axios";

const store = useStore();

interface QuestObjective {
  id: string;
  text: string;
  status: string;
  progress_current: number;
  progress_target: number;
}

interface QuestInstance {
  id: number;
  status: string;
  resolution: string | null;
  template: {
    slug: string;
    name: string;
    quest_type: string;
  };
  current_step: {
    recap: string;
    text: {
      body?: string;
    };
    objectives: QuestObjective[];
  };
  latest_journal_entry?: {
    recap?: string;
  } | null;
}

const quests = ref<QuestInstance[]>([]);
const expanded = ref<number | null>(null);
const fetched = ref<boolean>(false);
const selectedTab = ref<"active" | "resolved">("active");

const getQuests = async () => {
  const endpoint = selectedTab.value == "resolved"
    ? "/game/quests/resolved/"
    : "/game/quests/active/";

  const resp = await axios.get(endpoint, {
    headers: { "X-PLAYER-ID": store.state.game.player.id },
  });
  quests.value = resp.data.quests || [];
  fetched.value = true;
  expanded.value = quests.value.length ? quests.value[0].id : null;
};

const clickTab = async (tab: "active" | "resolved") => {
  selectedTab.value = tab;
  await getQuests();
};

const onClickName = (quest: QuestInstance) => {
  expanded.value = expanded.value == quest.id ? null : quest.id;
};

const visibleObjectives = (quest: QuestInstance) => {
  return (quest.current_step.objectives || []).filter((objective) => objective.status !== "hidden");
};

const objectiveProgress = (objective: QuestObjective) => {
  const current = Number(objective.progress_current || 0);
  const target = Number(objective.progress_target || 0);
  return target > 0 ? `${current}/${target}` : `${current}`;
};

const showQuestInfo = (quest: QuestInstance) => {
  if (!quest.template.slug) return;
  store.dispatch("game/cmd", `quest info ${quest.template.slug}`);
  closeQuestLog();
};

const closeQuestLog = () => {
  store.commit("ui/modal/close");
};

onMounted(async () => {
  await getQuests();
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#quest_log {
  padding: 15px;
  position: relative;

  .close-button {
    position: absolute;
    right: 0;
    top: -10px;
    padding: 20px;
  }

  h2 {
    margin: 0;
  }

  .quest-meta,
  .quest-section-label {
    font-size: 0.85rem;
    text-transform: uppercase;
  }

  .quest-objective {
    align-items: baseline;
    display: flex;
    gap: 0.75rem;
    justify-content: space-between;
  }

  .quest-progress {
    white-space: nowrap;
  }
}
</style>
