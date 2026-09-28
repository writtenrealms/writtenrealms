<template>
  <div class="quest-shell">

    <!-- Quest List -->
    <div v-if="questListSuccessMessage" class="indented">
      <div v-if="message.data.quests.length > 0">
        <div class="text-sm mb-1">{{ questListHeading }}</div>
        <div v-for="quest in message.data.quests" :key="quest.id">
          <span>
            <span class="quest-link" @click="runCommand(`quest info ${quest.template.slug}`)">{{ quest.template.name }}</span>
            <span class="color-text-50 ml-2">[ {{ quest.template.slug }} ]</span>
          </span>
        </div>
      </div>
      <div v-else>
        {{ questListEmptyText }}
      </div>
    </div>


    <!-- quest.instance.started -->
    <div v-else-if="startedQuest" class="quest-inline quest-inline-started">
      <span class="quest-inline-text">
        Quest <span class="quest-link" @click="runCommand(startedQuest.infoCommand)">{{ startedQuest.name }}</span> has started.
      </span>
      <div v-for="(line, index) in rewardLines" :key="`started-reward-${index}`" class="quest-inline-reward">
        {{ line }}
      </div>
    </div>

    <div v-else-if="updatedQuest" class="quest-inline quest-inline-updated">
      <span class="quest-inline-text">
        Quest <span class="quest-link" @click="runCommand(updatedQuest.infoCommand)">{{ updatedQuest.name }}</span> updated: {{ updatedQuest.objective.text }}
      </span>
      <span class="quest-inline-progress ml-2 color-text-50" :class="{ complete: updatedQuest.objective.status === 'complete' }">
        [ {{ updatedQuest.objective.progress }} ]
      </span>
      <div v-for="(line, index) in rewardLines" :key="`updated-reward-${index}`" class="quest-inline-reward">
        {{ line }}
      </div>
    </div>

    <div v-else-if="abandonedQuest">
      <span>
        You abandon <span class="quest-link">{{ abandonedQuest.name }}</span>.
      </span>
    </div>

    <div v-else-if="syntaxQuestError">
      {{ message.text }}
    </div>

    <div v-else
      class="indented quest-message"
      :class="[
        variantClass,
        { actionable: isLastMessage, },
      ]">
      <div v-if="!singleQuestInfoMessage" class="quest-kicker">{{ kicker }}</div>

      <div v-if="cards.length" class="quest-cards">

        <QuestCard v-for="card in cards" :key="card.key" :card="card"
          :actionable="isLastMessage" @command="runCommand" />
      </div>

      <div v-else class="quest-fallback">
        <div v-for="(line, index) in fallbackLines" :key="index">{{ line }}</div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import QuestCard from "@/components/game/QuestCard.vue";
import { buildQuestBadges as buildBadges, questInstanceCard, splitQuestLines as splitLines } from "@/core/questPresentation";
import { formatMoney } from "@/core/economy.ts";

const store = useStore();

const props = defineProps<{
  message: any;
}>();

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
const questSubcommand = computed(() => String(props.message?.data?.subcommand || ""));
const questListSuccessMessage = computed(() => {
  if (props.message.type !== "cmd.quest.success") return false;
  if (!Array.isArray(props.message?.data?.quests)) return false;
  return questSubcommand.value === "list" || questSubcommand.value === "resolved";
});
const singleQuestInfoMessage = computed(() => {
  if (props.message.type !== "cmd.quest.success") return false;
  if (questSubcommand.value !== "info") return false;
  return Boolean(questPayload.value);
});
const questListHeading = computed(() => {
  return questSubcommand.value === "resolved" ? "Resolved Quests:" : "Active Quests:";
});
const questListEmptyText = computed(() => {
  return questSubcommand.value === "resolved" ? "No resolved quests." : "No active quests.";
});
const syntaxQuestError = computed(() => {
  if (props.message.type !== "cmd.quest.error") return false;
  const code = String(props.message?.data?.code || "");
  return code === "usage" || code === "unknown_subcommand" || code === "ambiguous_subcommand";
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
  if (type === "cmd.quest.success") {
    if (questSubcommand.value === "resolved") return "Resolved Quests";
    if (questSubcommand.value === "list") return "Active Quests";
    if (questSubcommand.value === "info") return "Quest Info";
  }

  const rawText = String(props.message.text || "").toLowerCase();
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
  const textLine = splitLines(props.message.text).find((line) => line.startsWith("Rewards:"));
  const currencyRewards = Array.isArray(props.message?.data?.currency_rewards)
    ? props.message.data.currency_rewards
    : [];
  if (!currencyRewards.length) return textLine ? [textLine] : [];

  const authoredCurrencyDisplays = new Set(
    currencyRewards
      .map((reward: any) => String(reward?.display || "").trim())
      .filter(Boolean),
  );
  const otherRewards = String(textLine || "")
    .replace(/^Rewards:\s*/, "")
    .split(",")
    .map((reward) => reward.trim())
    .filter((reward) => reward && !authoredCurrencyDisplays.has(reward));
  const formattedCurrencies = currencyRewards.map((reward: any) => (
    formatMoney(reward, store.state.game.world?.economy)
  ));
  return [`Rewards: ${[...otherRewards, ...formattedCurrencies].join(", ")}`];
});

const startedQuest = computed(() => {
  if (props.message.type !== "quest.instance.started" || !questPayload.value) return null;

  return {
    name: questName.value,
    slug: questSlug.value,
    infoCommand: questSlug.value ? `quest info ${questSlug.value}` : "",
  };
});

const updatedQuest = computed(() => {
  if (props.message.type !== "quest.instance.updated" || !questPayload.value) return null;
  const updatedObjective = props.message?.data?.updated_objective;
  if (!updatedObjective) return null;

  return {
    name: questName.value,
    slug: questSlug.value,
    infoCommand: questSlug.value ? `quest info ${questSlug.value}` : "",
    objective: {
      text: String(updatedObjective.text || updatedObjective.id || "Objective"),
      progress: String(updatedObjective.progress || ""),
      status: String(updatedObjective.status || "active"),
    },
  };
});

const abandonedQuest = computed(() => {
  if (props.message.type !== "quest.instance.resolved" || !questPayload.value) return null;
  if (questPayload.value.resolution !== "abandoned") return null;

  return {
    name: questName.value,
  };
});

const cards = computed(() => {
  if (opportunities.value.length) {
    return opportunities.value.map((opportunity: any) => ({
      key: `opportunity-${opportunity.slug || opportunity.id}`,
      title: opportunity.name || opportunity.slug || "Quest Opportunity",
      slug: opportunity.slug || "",
      badges: buildBadges(opportunity.quest_type),
      body: String(opportunity?.text?.body || "").trim(),
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
    const card = questInstanceCard(quest);
    card.rewardLines = rewardLines.value;
    if (quest.status === "active" && card.slug && !singleQuestInfoMessage.value) {
      card.actions = [{ label: "INFO", command: `quest info ${card.slug}`, tone: "secondary" }];
    }
    return [card];
  }

  if (questList.value.length) {
    return questList.value.map((quest: any) => {
      const card = questInstanceCard(quest);
      card.body = "";
      card.choiceRows = [];
      if (card.slug) card.actions = [{ label: "INFO", command: `quest info ${card.slug}`, tone: "secondary" }];
      return card;
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
        body: String(props.message?.data?.hint || props.message.text || "").trim(),
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

.quest-link {
  color: $color-secondary;
  cursor: pointer;
  &:hover {
    border-bottom: 1px dotted $color-text-hex-50;
  }
}

.quest-message {
  .current-step-recap {
    border-bottom: 1px dashed $color-text-hex-30;
    border-top: 1px dashed $color-text-hex-30;
  }



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

  .quest-inline-progress {
    @include font-mono;
    color: $color-text-hex-60;
    font-size: 0.84rem;
    white-space: nowrap;

    &.complete {
      color: $color-green;
    }
  }

  .quest-inline-reward {
    color: $color-secondary;
    flex-basis: 100%;
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

  .quest-fallback { margin-top: 0.8rem; }

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
