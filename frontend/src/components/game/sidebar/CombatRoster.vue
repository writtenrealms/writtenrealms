<template>
  <section aria-label="Combat participants">
    <div class="combat-heading">
      <h3>Combat</h3>
      <span v-if="combat" class="round-number">Round {{ combat.round }}</span>
    </div>
    <p v-if="!combat" class="empty-combat">No active combat.</p>
    <template v-else>
      <div v-if="combat.status === 'paused'" class="combat-paused">Paused</div>
      <div v-for="group in rosterGroups" :key="group.label" class="roster-side">
        <h4 class="roster-label">{{ group.label }}</h4>
        <div class="roster-members">
          <button
            v-for="member in group.members"
            :key="member.key"
            type="button"
            class="roster-member"
            :class="{ selected: member.key === playerTarget?.key, ally: member.relation !== 'enemy' }"
            :disabled="member.relation !== 'enemy'"
            :aria-pressed="member.relation === 'enemy' ? member.key === playerTarget?.key : undefined"
            @click="store.dispatch('game/cmd', `kill ${member.key}`)"
          >
            <span class="roster-name">{{ member.name }}<span v-if="member.relation === 'self'"> (you)</span></span>
            <span class="roster-health">{{ member.health }} / {{ member.health_max }}</span>
            <span class="roster-meter" aria-hidden="true">
              <span :style="{ width: resourcePercent(member.health, member.health_max) + '%' }"></span>
            </span>
            <span v-if="member.current_target" class="roster-target">Attacking {{ combatName(member.current_target) }}</span>
            <span v-if="member.effects?.length" class="roster-effects">{{ member.effects.map(effect => effect.label || effect.effect).join(' · ') }}</span>
          </button>
        </div>
      </div>
    </template>
  </section>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { resourcePercent } from "@/core/utils";
import type { CombatSnapshot } from "@/core/combatState";

const store = useStore();
const combat = computed<CombatSnapshot | null>(() => store.state.game.combat?.current);
const playerTarget = computed(() => store.state.game.player_target);
const rosterGroups = computed(() => [
  { label: "Your side", members: combat.value?.participants.filter(member => ["self", "ally"].includes(member.relation)) || [] },
  { label: "Opponents", members: combat.value?.participants.filter(member => member.relation === "enemy") || [] },
]);
const combatName = (key: string) => combat.value?.participants.find(member => member.key === key)?.name || "an opponent";
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.combat-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;

  h3 { text-transform: uppercase; }
}

.round-number, .empty-combat, .combat-paused {
  @include font-text-regular;
  font-size: 12px;
  color: $color-text-hex-60;
}

.empty-combat, .combat-paused { margin-top: 8px; }

.roster-label {
  @include font-title-regular;
  margin: 15px 0 7px;
  font-size: 11px;
  text-transform: uppercase;
  color: $color-text-hex-60;
}

.roster-members { max-height: 260px; overflow-y: auto; }

.roster-member {
  @include font-text-regular;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 5px 8px;
  width: 100%;
  padding: 9px;
  margin-bottom: 5px;
  text-align: left;
  color: $color-text;
  background: transparent;
  border: 1px solid $color-background-border;
  cursor: pointer;

  &:disabled { opacity: 1; cursor: default; }
  &:not(:disabled):hover { border-color: $color-red; }
  &:focus-visible { outline: 2px solid $color-primary; outline-offset: -2px; }
  &.selected { border-color: $color-red; background: rgba($color-red, .08); }
}

.roster-name { grid-column: 1 / -1; font-size: 13px; overflow-wrap: anywhere; }
.roster-health { grid-column: 1 / -1; font-size: 11px; color: $color-text-hex-70; }
.roster-meter { grid-column: 1 / -1; height: 3px; background: $color-background-very-light; }
.roster-meter > span { display: block; height: 100%; background: $color-red; }
.ally .roster-meter > span { background: $color-green; }
.roster-target, .roster-effects {
  grid-column: 1 / -1;
  font-size: 11px;
  line-height: 16px;
  overflow-wrap: anywhere;
  color: $color-text-hex-60;
}
</style>
