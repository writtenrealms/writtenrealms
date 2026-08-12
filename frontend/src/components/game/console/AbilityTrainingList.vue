<template>
  <div class="ability-training-list indented">
    <div>{{ heading }}</div>

    <section
      v-for="group in trainingGroups"
      :key="group.key"
      class="training-group"
      :class="{ 'has-learning-status': group.learning }"
    >
      <div
        v-if="group.learning"
        class="learning-status"
        :class="{
          'at-cap': trainerLearningLimitReached(group.learning),
          denied: group.learning.status === 'denied',
        }"
      >
        {{ learningStatusText(group.learning) }}
      </div>

      <ol v-if="group.abilities.length" class="list mt-3">
        <li
          v-for="ability in group.abilities"
          :key="ability.key || ability.slug || ability.id"
          class="ability-entry"
        >
          <button
            type="button"
            class="ability-name"
            :class="{ interactive: canSelect(ability) }"
            :disabled="!canSelect(ability)"
            @click="selectAbility(ability)"
          >{{ ability.name || ability.slug }}</button>
          <span v-if="providerName(ability)" class="provider color-text-60">
            — {{ providerName(ability) }}
          </span>
        </li>
      </ol>
    </section>

    <div v-if="message.data?.truncated" class="color-text-50 font-text-light ml-2 mt-2">
      Only the first {{ message.data.limit }} abilities are shown.
    </div>

    <div v-if="!isPolicyError && capText" class="ability-cap mt-4" :class="{ 'at-cap': isAtCap }">
      {{ capText }}
    </div>
    <div v-if="!isPolicyError && abilities.length" class="training-hint color-text-50 font-text-light ml-2">
      Select an ability to {{ isUnlearn ? "unlearn" : "learn" }} it.
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import {
  trainingProviderIsAvailableInRoom,
  type TrainingProvider,
} from "@/core/trainingProviders";
import {
  trainerLearningChoiceIsAvailable,
  trainerLearningLimitReached,
  trainerLearningStatusesForMessage,
  trainerLearningStatusForAbility,
  trainerLearningStatusKey,
  trainerLearningStatusText,
  type TrainerLearningStatus,
} from "@/core/trainerLearning";

const store = useStore();
const props = defineProps<{ message: any }>();

const isUnlearn = computed(() => props.message.type === "cmd.ability.unlearn.list");
const isPolicyError = computed(() => (
  props.message.type === "cmd.ability.learn.error"
));
const abilities = computed<any[]>(() => (
  Array.isArray(props.message.data?.abilities) ? props.message.data.abilities : []
));
const heading = computed(() => {
  if (isPolicyError.value) return props.message.text || "You cannot learn that ability.";
  if (!abilities.value.length) {
    return isUnlearn.value
      ? "There is nothing you can unlearn here right now."
      : "There is nothing you can learn here right now.";
  }
  return isUnlearn.value ? "You can unlearn here:" : "You can learn here:";
});

interface AbilityTrainingGroup {
  key: string;
  learning: TrainerLearningStatus | null;
  abilities: any[];
}

const trainingGroups = computed<AbilityTrainingGroup[]>(() => {
  const groups = new Map<string, AbilityTrainingGroup>();
  for (const learning of trainerLearningStatusesForMessage(
    props.message.data,
    abilities.value,
  )) {
    const key = trainerLearningStatusKey(learning);
    groups.set(key, { key, learning, abilities: [] });
  }

  for (const ability of abilities.value) {
    const learning = trainerLearningStatusForAbility(ability, props.message.data);
    const key = learning ? trainerLearningStatusKey(learning) : "other-training";
    if (!groups.has(key)) groups.set(key, { key, learning, abilities: [] });
    groups.get(key)?.abilities.push(ability);
  }
  return [...groups.values()].filter((group) => (
    group.abilities.length || !isUnlearn.value
  ));
});
const learningStatusText = (status: TrainerLearningStatus): string => (
  trainerLearningStatusText(status, { unlearn: isUnlearn.value })
);
const isLastMessage = computed(() => (
  [...(store.state.game.messages || [])]
    .reverse()
    .find((message: any) => (
      String(message.type || "").startsWith("cmd.ability.learn.")
      || String(message.type || "").startsWith("cmd.ability.unlearn.")
    )) === props.message
));
const hasCapState = computed(() => Object.prototype.hasOwnProperty.call(
  props.message.data || {},
  "max_known",
));
const maxKnown = computed(() => {
  const rawValue = props.message.data?.max_known;
  if (rawValue === null || rawValue === undefined) return null;
  const value = Number(rawValue);
  return Number.isFinite(value) && value >= 0 ? value : null;
});
const knownCount = computed(() => {
  const known = store.state.game.player?.known_abilities
    ?? props.message.data?.actor?.known_abilities;
  return Array.isArray(known) ? known.length : 0;
});
const knownSlugs = computed(() => new Set(
  (store.state.game.player?.known_abilities || [])
    .map((slug: unknown) => String(slug || "").trim().toLowerCase())
    .filter(Boolean),
));
const isAtCap = computed(() => (
  !isUnlearn.value
  && maxKnown.value !== null
  && knownCount.value >= maxKnown.value
));
const capText = computed(() => {
  if (maxKnown.value === null) {
    return hasCapState.value
      ? `You know ${knownCount.value} abilities. Your ability limit is uncapped.`
      : "";
  }
  if (isAtCap.value) {
    return `You know the maximum of ${maxKnown.value} abilities. Unlearn one first.`;
  }
  return `You know ${knownCount.value} of ${maxKnown.value} abilities.`;
});

const providerFor = (ability: any): TrainingProvider | null => (
  ability?.trainer
  || ability?.provider
  || ability?.training_provider
  || props.message.data?.trainer
  || props.message.data?.training_provider
  || null
);

const providerName = (ability: any): string => (
  String(providerFor(ability)?.name || "").trim()
);

const canSelect = (ability: any): boolean => (
  isLastMessage.value
  && (!isAtCap.value || isUnlearn.value)
  && (isUnlearn.value || trainerLearningChoiceIsAvailable(
    trainerLearningStatusForAbility(ability, props.message.data),
  ))
  && (isUnlearn.value
    ? knownSlugs.value.has(String(ability?.slug || "").toLowerCase())
    : !knownSlugs.value.has(String(ability?.slug || "").toLowerCase()))
  && trainingProviderIsAvailableInRoom(
    providerFor(ability),
    store.state.game.room,
  )
);

const commandFor = (ability: any): string => {
  const authoredCommand = isUnlearn.value
    ? ability?.unlearn_command
    : ability?.learn_command;
  const genericCommand = ability?.command;
  if (String(authoredCommand || genericCommand || "").trim()) {
    return String(authoredCommand || genericCommand).trim();
  }
  const selector = String(
    ability?.command_selector || ability?.slug || ability?.name || "",
  ).trim();
  return `${isUnlearn.value ? "unlearn" : "learn"} ${selector}`.trim();
};

const selectAbility = (ability: any) => {
  if (!canSelect(ability)) return;
  const command = commandFor(ability);
  if (command) store.dispatch("game/cmd", command);
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.ability-name {
  appearance: none;
  background: transparent;
  border: 0;
  border-bottom: 1px dotted #888;
  color: inherit;
  font: inherit;
  padding: 0;
  text-align: left;
}

.ability-name:disabled {
  border-bottom-color: transparent;
  color: inherit;
  cursor: default;
  opacity: 0.55;
}

.training-group {
  margin-top: 1rem;
}

.learning-status {
  font-weight: 600;
}

.at-cap,
.denied {
  color: $color-secondary;
}
</style>
