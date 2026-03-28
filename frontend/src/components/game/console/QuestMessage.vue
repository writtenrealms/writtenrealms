<template>
  <div class="quest-message indented" :class="[variantClass, { actionable: isLastMessage }]">
    <div class="quest-shell">

      <!-- quest.instance.started -->
      <div v-if="startedQuest" class="quest-inline quest-inline-started">
        <span class="quest-inline-text">
          Quest <span class="color-secondary">{{ startedQuest.name }}</span> has started.
        </span>
        <button
          v-if="isLastMessage && startedQuest.infoCommand"
          class="btn-small secondary"
          @click="runCommand(startedQuest.infoCommand)"
        >
          INFO
        </button>
      </div>

      <template v-else>
        <div class="quest-kicker">{{ kicker }}</div>

        <div v-if="cards.length" class="quest-cards">

        <article v-for="card in cards" :key="card.key" class="quest-card">
          <div class="quest-card-header">
            <div>
              <div class="quest-title">{{ card.title }}</div>
              <div v-if="card.slug" class="quest-slug">{{ card.slug }}</div>
            </div>

            <div v-if="card.badges.length" class="quest-badges">
              <span
                v-for="badge in card.badges"
                :key="badge.label"
                class="quest-badge"
                :class="badge.tone"
              >
                {{ badge.label }}
              </span>
            </div>
          </div>

          <div v-if="card.bodyLines.length" class="quest-body">
            <div v-for="(line, index) in card.bodyLines" :key="index">{{ line }}</div>
          </div>

          <div v-if="card.recapLines.length" class="quest-recap">
            <div class="quest-section-label">Recap</div>
            <div v-for="(line, index) in card.recapLines" :key="index">{{ line }}</div>
          </div>

          <div v-if="card.objectives.length" class="quest-objectives">
            <div class="quest-section-label">Objectives</div>
            <div
              v-for="objective in card.objectives"
              :key="objective.id"
              class="quest-objective"
              :class="objective.status"
            >
              <div class="quest-objective-copy">
                <div class="quest-objective-text">{{ objective.text }}</div>
                <div class="quest-objective-progress">{{ objective.progress }}</div>
              </div>
              <span class="quest-objective-status">{{ objective.statusLabel }}</span>
            </div>
          </div>

          <div v-if="card.choiceRows.length" class="quest-choices">
            <div class="quest-section-label">Choices</div>
            <div v-for="choice in card.choiceRows" :key="choice.id" class="quest-choice">
              <div class="quest-choice-text">{{ choice.text }}</div>
              <button
                v-if="isLastMessage && choice.command"
                class="btn-small"
                @click="runCommand(choice.command)"
              >
                CHOOSE
              </button>
            </div>
          </div>

          <div v-if="card.metaLines.length" class="quest-meta">
            <div v-for="(line, index) in card.metaLines" :key="index">{{ line }}</div>
          </div>

          <div v-if="card.rewardLines.length" class="quest-rewards">
            <div v-for="(line, index) in card.rewardLines" :key="index">{{ line }}</div>
          </div>

          <div v-if="isLastMessage && card.actions.length" class="quest-actions">
            <button
              v-for="action in card.actions"
              :key="action.command"
              class="btn-small"
              :class="action.tone"
              @click="runCommand(action.command)"
            >
              {{ action.label }}
            </button>
          </div>
        </article>
        </div>

        <div v-else class="quest-fallback">
          <div v-for="(line, index) in fallbackLines" :key="index">{{ line }}</div>
        </div>
      </template>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";

const store = useStore();

const props = defineProps<{
  message: any;
}>();

const splitLines = (value: any) => {
  return String(value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
};

const isLastMessage = computed(() => {
  return store.state.game.last_message[props.message.type] == props.message;
});

const questPayload = computed(() => props.message?.data?.quest || null);
const questTemplate = computed(() => questPayload.value?.template || null);
const questName = computed(() => {
  const template = questTemplate.value || {};
  return template.name || template.slug || "Quest";
});
const questSlug = computed(() => questTemplate.value?.slug || "");
const opportunities = computed(() => {
  const data = props.message?.data || {};
  if (Array.isArray(data.opportunities)) return data.opportunities;
  if (data.opportunity) return [data.opportunity];
  return [];
});
const questList = computed(() => {
  const quests = props.message?.data?.quests;
  return Array.isArray(quests) ? quests : [];
});

const kicker = computed(() => {
  const type = props.message.type;
  if (type === "quest.opportunity.presented") return "Quest Available";
  if (type === "quest.opportunity.available") return "New Opportunity";
  if (type === "quest.interaction.hint") return "Quest Hint";
  if (type === "quest.instance.started") return "Quest Started";
  if (type === "quest.instance.updated") return "Quest Updated";
  if (type === "quest.instance.resolved") return "Quest Resolved";
  if (type === "cmd.quest.error") return "Quest Error";

  const rawText = String(props.message.text || "").toLowerCase();
  if (rawText.startsWith("opportunities:")) return "Quest Opportunities";
  if (rawText.startsWith("resolved quests:")) return "Resolved Quests";
  if (rawText.startsWith("active quests:")) return "Active Quests";
  return "Quest";
});

const variantClass = computed(() => {
  const type = props.message.type;
  if (type === "cmd.quest.error") return "is-error";
  if (type === "quest.instance.resolved") return "is-resolved";
  if (type === "quest.opportunity.presented" || type === "quest.opportunity.available") return "is-opportunity";
  return "is-neutral";
});

const rewardLines = computed(() => {
  return splitLines(props.message.text).filter((line) => line.startsWith("Rewards:"));
});

const startedQuest = computed(() => {
  if (props.message.type !== "quest.instance.started" || !questPayload.value) return null;

  return {
    name: questName.value,
    slug: questSlug.value,
    infoCommand: questSlug.value ? `quest info ${questSlug.value}` : "",
  };
});

const buildBadges = (questType: string | null, status: string | null, resolution: string | null) => {
  const badges: any[] = [];
  if (questType) {
    badges.push({ label: questType, tone: "tone-type" });
  }
  if (status === "active") {
    badges.push({ label: "active", tone: "tone-active" });
  }
  if (status === "resolved") {
    badges.push({ label: resolution || "resolved", tone: "tone-resolved" });
  }
  return badges;
};

const buildObjectives = (objectives: any[] | undefined) => {
  return (objectives || [])
    .filter((objective) => objective && objective.status !== "hidden")
    .map((objective) => {
      const current = Number(objective.progress_current || 0);
      const target = Number(objective.progress_target || 0);
      const status = String(objective.status || "active");
      return {
        id: objective.id || objective.text,
        text: objective.text || objective.id || "Objective",
        progress: target > 0 ? `${current}/${target}` : `${current}`,
        status,
        statusLabel: status.replace(/_/g, " "),
      };
    });
};

const cards = computed(() => {
  if (opportunities.value.length) {
    return opportunities.value.map((opportunity: any) => ({
      key: `opportunity-${opportunity.slug || opportunity.id}`,
      title: opportunity.name || opportunity.slug || "Quest Opportunity",
      slug: opportunity.slug || "",
      badges: buildBadges(opportunity.quest_type || null, null, null),
      bodyLines: splitLines(opportunity?.text?.body),
      recapLines: splitLines(opportunity.recap),
      objectives: [],
      choiceRows: [],
      metaLines: [],
      rewardLines: [],
      actions: opportunity.slug
        ? [{ label: "ACCEPT", command: `quest accept ${opportunity.slug}`, tone: "primary" }]
        : [],
    }));
  }

  if (questPayload.value) {
    const quest = questPayload.value;
    const template = quest.template || {};
    const currentStep = quest.current_step || {};
    return [
      {
        key: `quest-${quest.id || template.slug || "current"}`,
        title: template.name || template.slug || "Quest",
        slug: template.slug || "",
        badges: buildBadges(template.quest_type || null, quest.status || null, quest.resolution || null),
        bodyLines: splitLines(currentStep?.text?.body),
        recapLines: splitLines(currentStep.recap),
        objectives: buildObjectives(currentStep.objectives),
        choiceRows: (currentStep.choices || []).map((choice: any) => ({
          id: choice.id,
          text: choice.text || choice.id,
          command: template.slug ? `quest choose ${template.slug} ${choice.id}` : "",
        })),
        metaLines: [],
        rewardLines: rewardLines.value,
        actions: quest.status === "active" && template.slug
          ? [{ label: "INFO", command: `quest info ${template.slug}`, tone: "secondary" }]
          : [],
      },
    ];
  }

  if (questList.value.length) {
    return questList.value.map((quest: any) => {
      const template = quest.template || {};
      const currentStep = quest.current_step || {};
      return {
        key: `quest-list-${quest.id || template.slug || template.name}`,
        title: template.name || template.slug || "Quest",
        slug: template.slug || "",
        badges: buildBadges(template.quest_type || null, quest.status || null, quest.resolution || null),
        bodyLines: [],
        recapLines: splitLines(currentStep.recap),
        objectives: buildObjectives(currentStep.objectives),
        choiceRows: [],
        metaLines: [],
        rewardLines: [],
        actions: template.slug
          ? [{ label: "INFO", command: `quest info ${template.slug}`, tone: "secondary" }]
          : [],
      };
    });
  }

  if (props.message.type === "quest.interaction.hint") {
    const targetName = props.message?.data?.target?.name || "Quest";
    return [
      {
        key: `hint-${targetName}`,
        title: targetName,
        slug: "",
        badges: [],
        bodyLines: splitLines(props.message?.data?.hint || props.message.text),
        recapLines: [],
        objectives: [],
        choiceRows: [],
        metaLines: [],
        rewardLines: [],
        actions: [],
      },
    ];
  }

  return [];
});

const fallbackLines = computed(() => splitLines(props.message.text));

const runCommand = (command: string) => {
  if (!command) return;
  store.dispatch("game/cmd", command);
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.quest-message {
  // margin-top: 1rem;

  .quest-kicker {
    @include font-title-regular;
    color: $color-secondary;
    font-size: 0.82rem;
    letter-spacing: 1.6px;
    margin-bottom: 0.65rem;
    text-transform: uppercase;
  }

  .quest-inline {
    align-items: center;
    color: $color-text;
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
  }

  .quest-inline-text {
    color: $color-text;
  }

  .quest-inline-name {
    @include font-title-regular;
    color: $color-text;
  }

  .quest-cards {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
  }

  .quest-card {
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0.01));
    border: 1px solid $color-background-border;
    border-radius: 6px;
    padding: 0.9rem 1rem;
  }

  .quest-card-header {
    display: flex;
    gap: 1rem;
    justify-content: space-between;
    align-items: flex-start;
  }

  .quest-title {
    @include font-title-regular;
    color: $color-text;
    font-size: 1.05rem;
    line-height: 1.2;
  }

  .quest-slug {
    @include font-mono;
    color: $color-text-hex-60;
    font-size: 0.84rem;
    margin-top: 0.2rem;
  }

  .quest-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    justify-content: flex-end;
  }

  .quest-badge {
    @include font-title-regular;
    border-radius: 999px;
    border: 1px solid $color-background-border;
    color: $color-text-hex-70;
    font-size: 0.68rem;
    letter-spacing: 1px;
    padding: 0.18rem 0.55rem;
    text-transform: uppercase;

    &.tone-active {
      border-color: rgba(39, 144, 132, 0.5);
      color: $color-green;
    }

    &.tone-resolved {
      border-color: rgba(245, 201, 131, 0.35);
      color: $color-secondary;
    }

    &.tone-type {
      color: $color-text-hex-60;
    }
  }

  .quest-body,
  .quest-recap,
  .quest-meta,
  .quest-rewards,
  .quest-fallback {
    margin-top: 0.8rem;
  }

  .quest-body {
    color: $color-text;
  }

  .quest-recap {
    color: $color-text-hex-70;
  }

  .quest-section-label {
    @include font-title-regular;
    color: $color-text-hex-60;
    font-size: 0.72rem;
    letter-spacing: 1.2px;
    margin-bottom: 0.35rem;
    text-transform: uppercase;
  }

  .quest-objectives,
  .quest-choices {
    margin-top: 0.85rem;
  }

  .quest-objective,
  .quest-choice {
    align-items: center;
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 4px;
    display: flex;
    gap: 0.75rem;
    justify-content: space-between;
    margin-top: 0.4rem;
    padding: 0.55rem 0.65rem;
  }

  .quest-objective-copy {
    min-width: 0;
  }

  .quest-objective-text,
  .quest-choice-text {
    color: $color-text;
  }

  .quest-objective-progress {
    @include font-mono;
    color: $color-text-hex-60;
    font-size: 0.84rem;
    margin-top: 0.1rem;
  }

  .quest-objective-status {
    @include font-title-regular;
    color: $color-text-hex-60;
    font-size: 0.68rem;
    letter-spacing: 1px;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .quest-objective.complete .quest-objective-status {
    color: $color-green;
  }

  .quest-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.55rem;
    margin-top: 0.9rem;
  }

  .quest-meta {
    color: $color-text-hex-60;
  }

  .quest-rewards {
    color: $color-secondary;
  }

  &.is-error {
    .quest-shell {
      border-left-color: $color-red;
    }

    .quest-kicker {
      color: $color-red;
    }
  }

  &.is-opportunity {
    .quest-shell {
      border-left-color: rgba(245, 201, 131, 0.5);
    }
  }

  &.is-resolved {
    .quest-shell {
      border-left-color: rgba(39, 144, 132, 0.45);
    }
  }
}
</style>
