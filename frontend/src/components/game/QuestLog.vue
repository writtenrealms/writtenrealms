<template>
  <div id="quest_log">
    <button type="button" class="close-button hover" aria-label="Close" @click="closeQuestLog">
      <span aria-hidden="true">&#10006;</span>
    </button>

    <div class="my-4">
      <h1 class="mb-4">Quest Log</h1>

      <div v-if="loading" class="quest-log-message" role="status">
        Loading quest log...
      </div>
      <div v-else-if="loadError" class="quest-log-message" role="alert">
        <div>Could not load the Quest Log.</div>
        <button type="button" class="btn-small mt-2" @click="getQuests">RETRY</button>
      </div>
      <template v-else>
        <div class="tabs-view">
          <div class="tabs quest-tabs" role="tablist" aria-label="Quest status">
            <button
              v-for="tab in questTabs"
              :id="tabId(tab.id)"
              :key="tab.id"
              type="button"
              role="tab"
              class="tab-item"
              :class="{ activeTab: selectedTab == tab.id }"
              :aria-controls="panelId()"
              :aria-selected="selectedTab == tab.id"
              :tabindex="selectedTab == tab.id ? 0 : -1"
              @click="clickTab(tab.id)"
              @keydown="onTabKeydown($event, tab.id)"
            >
              {{ tab.label }}
            </button>
          </div>
        </div>

        <div
          :id="panelId()"
          class="quest-tab-panel"
          role="tabpanel"
          :aria-labelledby="tabId(selectedTab)"
          tabindex="0"
        >
          <div v-if="selectedLimit.truncated" class="quest-limit-notice color-text-50">
            Showing the {{ selectedLimit.limit }} most recent quests.
          </div>

          <template v-if="quests.length">
            <div v-for="quest in quests" :key="quest.id" class="quest-entry mt-4">
              <div class="quest-heading">
                <h2 class="mb-2">
                  <button
                    type="button"
                    class="quest-title-button"
                    :aria-controls="questDetailsId(quest)"
                    :aria-expanded="expanded == quest.id"
                    @click="onClickName(quest)"
                  >
                    <span class="interactive">{{ quest.template.name }}</span>
                    <span class="ml-2 color-text-50" v-if="store.state.game.player.is_builder">[ {{ quest.id }} ]</span>
                  </button>
                </h2>
                <span v-if="selectedTab == 'repeatable'" class="repeatable-badge">
                  Repeatable Quest
                </span>
              </div>
              <div
                v-if="selectedTab == 'repeatable'"
                class="repeatability-status"
                :class="repeatabilityStatusClass(quest)"
                :title="repeatabilityReadyAtTitle(quest)"
              >
                {{ repeatabilityStatus(quest) }}
              </div>
              <div v-show="expanded == quest.id" :id="questDetailsId(quest)">
                <div class="quest-meta color-text-50">
                  {{ selectedTab == "repeatable" ? "Repeatable Quest" : quest.status }}
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
              </div>
            </div>
          </template>
          <template v-else>
            <div class="mt-6" v-if="selectedTab == 'resolved'">No resolved quests.</div>
            <div class="mt-6" v-else-if="selectedTab == 'repeatable'">No repeatable quests.</div>
            <div class="mt-6" v-else>No active quests.</div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
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
  repeatability: {
    mode: "never" | "always" | "cooldown";
    cooldown_seconds: number;
    state: "waiting" | "ready" | "unavailable";
    ready_at: string | null;
    remaining_seconds: number | null;
    template_status: string;
  };
}

type QuestLogTab = "active" | "repeatable" | "resolved";

const questTabs: { id: QuestLogTab; label: string }[] = [
  { id: "active", label: "Active" },
  { id: "repeatable", label: "Repeatable" },
  { id: "resolved", label: "Resolved" },
];

interface QuestLogResponse {
  active?: QuestInstance[];
  repeatable?: QuestInstance[];
  resolved?: QuestInstance[];
  server_time?: string;
  limits?: Partial<Record<QuestLogTab, QuestLogLimit>>;
}

interface QuestLogLimit {
  limit: number;
  truncated: boolean;
}

const questGroups = ref<Record<QuestLogTab, QuestInstance[]>>({
  active: [],
  repeatable: [],
  resolved: [],
});
const questLimits = ref<Record<QuestLogTab, QuestLogLimit>>({
  active: { limit: 0, truncated: false },
  repeatable: { limit: 0, truncated: false },
  resolved: { limit: 0, truncated: false },
});
const expanded = ref<number | null>(null);
const loading = ref(true);
const loadError = ref(false);
const selectedTab = ref<QuestLogTab>("active");
const nowMs = ref(Date.now());
const fetchedAtMs = ref(Date.now());
const serverTimeAtFetchMs = ref<number | null>(null);
let countdownTimer: number | null = null;

const quests = computed(() => questGroups.value[selectedTab.value]);
const selectedLimit = computed(() => questLimits.value[selectedTab.value]);

const getQuests = async () => {
  loading.value = true;
  loadError.value = false;
  try {
    const resp = await axios.get<QuestLogResponse>("/game/quests/log/", {
      headers: { "X-PLAYER-ID": store.state.game.player.id },
    });
    questGroups.value = {
      active: resp.data.active || [],
      repeatable: resp.data.repeatable || [],
      resolved: resp.data.resolved || [],
    };
    questLimits.value = {
      active: resp.data.limits?.active || { limit: 0, truncated: false },
      repeatable: resp.data.limits?.repeatable || { limit: 0, truncated: false },
      resolved: resp.data.limits?.resolved || { limit: 0, truncated: false },
    };
    fetchedAtMs.value = Date.now();
    nowMs.value = fetchedAtMs.value;
    const parsedServerTime = Date.parse(resp.data.server_time || "");
    serverTimeAtFetchMs.value = Number.isNaN(parsedServerTime) ? null : parsedServerTime;
    expanded.value = quests.value.length ? quests.value[0].id : null;
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
};

const clickTab = (tab: QuestLogTab) => {
  selectedTab.value = tab;
  expanded.value = quests.value.length ? quests.value[0].id : null;
};

const tabId = (tab: QuestLogTab) => `quest-log-tab-${tab}`;
const panelId = () => "quest-log-panel";

const focusTab = (tab: QuestLogTab) => {
  clickTab(tab);
  window.requestAnimationFrame(() => document.getElementById(tabId(tab))?.focus());
};

const onTabKeydown = (event: KeyboardEvent, tab: QuestLogTab) => {
  const currentIndex = questTabs.findIndex((candidate) => candidate.id === tab);
  if (event.key === "Home") {
    event.preventDefault();
    focusTab(questTabs[0].id);
  } else if (event.key === "End") {
    event.preventDefault();
    focusTab(questTabs[questTabs.length - 1].id);
  } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const offset = event.key === "ArrowLeft" ? -1 : 1;
    const nextIndex = (currentIndex + offset + questTabs.length) % questTabs.length;
    focusTab(questTabs[nextIndex].id);
  }
};

const onClickName = (quest: QuestInstance) => {
  expanded.value = expanded.value == quest.id ? null : quest.id;
};

const questDetailsId = (quest: QuestInstance) => `quest-log-details-${quest.id}`;

const visibleObjectives = (quest: QuestInstance) => {
  return (quest.current_step.objectives || []).filter((objective) => objective.status !== "hidden");
};

const objectiveProgress = (objective: QuestObjective) => {
  const current = Number(objective.progress_current || 0);
  const target = Number(objective.progress_target || 0);
  return target > 0 ? `${current}/${target}` : `${current}`;
};

const repeatabilityRemainingSeconds = (quest: QuestInstance) => {
  const repeatability = quest.repeatability;
  if (!repeatability || repeatability.state !== "waiting") return 0;

  const elapsedMs = nowMs.value - fetchedAtMs.value;
  const parsedReadyAt = Date.parse(repeatability.ready_at || "");
  if (serverTimeAtFetchMs.value !== null && !Number.isNaN(parsedReadyAt)) {
    const currentServerTime = serverTimeAtFetchMs.value + elapsedMs;
    return Math.max(0, Math.ceil((parsedReadyAt - currentServerTime) / 1000));
  }

  const initialRemaining = Number(repeatability.remaining_seconds || 0);
  const elapsed = Math.floor(elapsedMs / 1000);
  return Math.max(0, initialRemaining - elapsed);
};

const formatDuration = (totalSeconds: number) => {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  const parts: string[] = [];

  if (days) parts.push(`${days}d`);
  if (hours || days) parts.push(`${hours}h`);
  if (minutes || hours || days) parts.push(`${minutes}m`);
  parts.push(`${remainder}s`);
  return parts.join(" ");
};

const repeatabilityStatus = (quest: QuestInstance) => {
  const repeatability = quest.repeatability;
  if (!repeatability || repeatability.state === "unavailable") {
    return "Repeatable quest currently unavailable";
  }

  const remaining = repeatabilityRemainingSeconds(quest);
  if (repeatability.state === "waiting" && remaining > 0) {
    return `Ready to repeat in ${formatDuration(remaining)}`;
  }
  return "Ready to repeat";
};

const repeatabilityStatusClass = (quest: QuestInstance) => {
  if (!quest.repeatability || quest.repeatability.state === "unavailable") {
    return "is-unavailable";
  }
  return repeatabilityRemainingSeconds(quest) > 0 ? "is-waiting" : "is-ready";
};

const repeatabilityReadyAtTitle = (quest: QuestInstance) => {
  if (quest.repeatability?.state !== "waiting") return "";

  const readyAt = quest.repeatability.ready_at;
  if (!readyAt) return "";

  const parsedReadyAt = new Date(readyAt);
  if (Number.isNaN(parsedReadyAt.getTime())) return "";
  return `Ready at ${parsedReadyAt.toLocaleString()}`;
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
  countdownTimer = window.setInterval(() => {
    nowMs.value = Date.now();
  }, 1000);
  await getQuests();
});

onBeforeUnmount(() => {
  if (countdownTimer !== null) window.clearInterval(countdownTimer);
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

#quest_log {
  padding: 15px;
  position: relative;

  .close-button {
    appearance: none;
    background: transparent;
    border: 0;
    color: inherit;
    font: inherit;
    position: absolute;
    right: 0;
    top: -10px;
    padding: 20px;
  }

  h2 {
    margin: 0;
  }

  .quest-tabs {
    overflow-x: auto;
    padding: 0;

    .tab-item {
      appearance: none;
      background: transparent;
      border: 2px solid transparent;
      flex: 0 0 auto;
      font: inherit;
      white-space: nowrap;

      &.activeTab {
        border-color: $color-background-light;
        border-bottom-color: transparent;
      }
    }
  }

  .quest-heading {
    align-items: baseline;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .quest-title-button {
    appearance: none;
    background: transparent;
    border: 0;
    color: inherit;
    font: inherit;
    padding: 0;
    text-align: left;
  }

  .quest-log-message {
    margin-top: 1rem;
  }

  .quest-limit-notice {
    font-size: 0.85rem;
    margin-top: 0.75rem;
  }

  .repeatable-badge {
    border: 1px solid $color-secondary;
    color: $color-secondary;
    font-size: 0.7rem;
    padding: 0.1rem 0.35rem;
    text-transform: uppercase;
  }

  .repeatability-status {
    font-size: 0.9rem;
    margin-bottom: 0.5rem;

    &.is-ready {
      color: $color-secondary;
    }

    &.is-waiting,
    &.is-unavailable {
      color: $color-text-hex-50;
    }
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
