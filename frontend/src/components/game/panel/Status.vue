<template>
  <div v-if="player" class="status-region">
    <div class="player-status">
      <div class="state-region">
        <div
          class="player-state"
          :class="{ [player.state]: true, interactive: (player.state == 'standing' || player.state == 'resting')}"
          @click="onClickState"
        >{{ player_state }}</div>
      </div>
      <div
        v-if="active_effects.length || player_effects.length"
        class="own-effects-region"
      >
        <div
          v-for="effect in active_effects"
          :key="effect.key"
          class="round-effect"
          :class="[`effect-${effect.category}`]"
          :title="effect.title"
          :aria-label="effect.title"
          :style="{ '--effect-fill': effect.fillWidth }"
        >
          <span class="round-effect-fill"></span>
          <span class="round-effect-label">{{ effect.label }}</span>
          <span class="round-effect-remaining">{{ effect.roundsLabel }}</span>
        </div>
        <ProgressBar
          v-for="effect in player_effects"
          :key="effect.expires"
          :duration="effect_duration(effect)"
          :label="effect.code"
          :expires="effect.expires"
          method="channel"
        />
      </div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from 'vuex';
import { capfirst } from "@/core/utils";
import {
  presentRoundEffects,
  type ActiveRoundEffect,
} from "@/core/roundEffects";
import ProgressBar from "@/components/game/ProgressBar.vue";

const store = useStore();

const player = computed(() => store.state.game.player);
const player_effects = computed(() => {
  if (!player.value?.key) return [];
  return store.state.game.effects[player.value.key] || [];
});
const player_state = computed(() => capfirst(player.value?.state || ""));
const active_effects = computed(() => {
  const character_effects = Array.isArray(player.value?.active_effects)
    ? player.value.active_effects
    : [];
  const combat_effects = Array.isArray(player.value?.combat_effects)
    ? player.value.combat_effects
    : [];
  return presentRoundEffects(
    [...character_effects, ...combat_effects] as ActiveRoundEffect[],
  );
});
const effect_duration = (effect) => {
  const current = new Date().getTime();
  const elapsed = (current - effect.start) / 1000;
  const effect_duration = effect.duration - elapsed;
  return effect_duration;
};

const onClickState = () => {
  if (player.value.state === "standing") {
    store.dispatch("game/cmd", "rest");
  } else if (player.value.state === "resting") {
    store.dispatch("game/cmd", "stand");
  }
};
</script>

<style lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.status-region {
  padding: 0 20px;

  .player-status {
    display: flex;
    border-bottom: 1px solid $color-background-light;
    padding: 10px 0;

    .state-region {
      margin-right: 10px;
      // Weird quirk, if we don't do this then when own effects
      // show up there is a random 2px jump in the status
      // region.
      padding: 1px 0;

      .player-state {
        @include font-title-light;
        //color: $color-text-hex-50;
        font-size: 15px;
        line-height: 18px;

        &.standing:hover,
        &.resting:hover {
          cursor: pointer;
        }

        &.combat {
          color: $color-red;
        }
      }
    }

    .own-effects-region {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      min-width: 0;

      .progress-bar:not(:last-child) {
        margin-right: 10px;
      }

      .round-effect {
        @include font-text-regular;
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        max-width: 100%;
        min-height: 20px;
        padding: 1px 8px;
        border: 1px solid $color-green-dark;
        border-radius: 8px;
        background: $color-green-dark;
        color: $color-text;
        font-size: 12px;
        line-height: 16px;
        overflow: hidden;
        white-space: nowrap;
      }

      .round-effect-fill {
        position: absolute;
        top: 0;
        bottom: 0;
        left: 0;
        width: var(--effect-fill);
        background: $color-green;
      }

      .round-effect.effect-debuff {
        border-color: $color-red;
        background: $color-red-70;

        .round-effect-fill {
          background: $color-red;
        }
      }

      .round-effect-label {
        position: relative;
        z-index: 1;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .round-effect-remaining {
        position: relative;
        z-index: 1;
        margin-left: 6px;
        color: $color-text-70;
        font-size: 10px;
      }
    }
  }
}
</style>
