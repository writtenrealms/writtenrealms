<template>
  <article class="quest-card">
    <div class="quest-card-header">
      <div class="quest-heading">
        <h3 class="quest-title">
          <button v-if="collapsible" type="button" class="quest-title-button"
            :aria-expanded="expanded" :aria-controls="detailsId" @click="emit('toggle')">
            <span class="quest-disclosure" aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
            {{ card.title.toUpperCase() }}
          </button>
          <template v-else>{{ card.title.toUpperCase() }}</template>
          <span v-if="reference" class="quest-reference">{{ reference }}</span>
        </h3>
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

    <slot name="summary" />

    <div :id="detailsId" v-show="!collapsible || expanded">
      <template v-if="!collapsible || expanded">
        <div v-if="card.body" class="quest-body">{{ card.body }}</div>

        <div v-if="card.recapLines.length" class="quest-recap">
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
            </div>
            <span class="quest-objective-status" :class="{ complete: objective.status === 'complete' }">
              {{ objective.progress }}
            </span>
          </div>
        </div>

        <div v-if="card.choiceRows.length" class="quest-choices">
          <div class="quest-section-label">Choices</div>
          <div v-for="choice in card.choiceRows" :key="choice.id" class="quest-choice">
            <div class="quest-choice-text">{{ choice.text }}</div>
            <button
              v-if="actionable && choice.command"
              type="button"
              class="btn-small"
              @click="emit('command', choice.command)"
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

        <div v-if="actionable && card.actions.length" class="quest-actions">
          <button
            v-for="action in card.actions"
            :key="action.command"
            type="button"
            class="btn-small"
            :class="action.tone"
            @click="emit('command', action.command)"
          >
            {{ action.label }}
          </button>

        </div>
      </template>
    </div>
  </article>
</template>

<script lang="ts" setup>
import type { QuestCardData } from "@/core/questPresentation";

withDefaults(defineProps<{
  card: QuestCardData;
  actionable?: boolean;
  collapsible?: boolean;
  expanded?: boolean;
  detailsId?: string;
  reference?: string;
}>(), { actionable: false, collapsible: false, expanded: true });

const emit = defineEmits<{
  command: [command: string];
  toggle: [];
}>();
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

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
.quest-rewards {
  margin-top: 0.8rem;
}

.quest-recap {
  border-bottom: 1px dashed $color-text-hex-30;
  border-top: 1px dashed $color-text-hex-30;
  padding-top: 0.5rem;
  padding-bottom: 0.5rem;
}

.quest-body {
  color: $color-text;
  white-space: pre-line;
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
  @include font-mono;
  color: $color-text-hex-60;
  font-size: 0.84rem;
  white-space: nowrap;
}

.quest-objective-status.complete {
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

.quest-heading {
  min-width: 0;
}

.quest-title,
.quest-slug,
.quest-body,
.quest-recap,
.quest-meta,
.quest-objective-text,
.quest-choice-text {
  overflow-wrap: anywhere;
}

.quest-title {
  margin: 0;
}

.quest-title-button {
  appearance: none;
  background: transparent;
  border: 0;
  color: inherit;
  font: inherit;
  letter-spacing: inherit;
  padding: 0;
  text-align: left;
  cursor: pointer;

  &:hover { color: $color-secondary; }
  &:focus-visible { outline: 1px solid $color-secondary; outline-offset: 4px; }
}

.quest-disclosure,
.quest-reference {
  color: $color-text-hex-60;
  font-size: 0.8rem;
}

.quest-disclosure { margin-right: 0.25rem; }
.quest-reference { margin-left: 0.5rem; white-space: nowrap; }
.quest-badge,
.quest-objective-status { flex-shrink: 0; }
.quest-choice-text { min-width: 0; }
.quest-choice .btn-small { flex-shrink: 0; }

@media (max-width: 480px) {
  .quest-card-header { flex-wrap: wrap; gap: 0.5rem; }
  .quest-badges { justify-content: flex-start; }
}
</style>
