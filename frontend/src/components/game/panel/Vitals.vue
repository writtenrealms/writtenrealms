<template>
  <div class="vitals-view flex grow flex-col">
    <div class="vitals-region">
      <div class="vitals">
        <div class="vital health">
          <div class="label-row">
            <div class="label">{{ healthLabel }}</div>
            <div class="amount">{{ player.health }}</div>
          </div>
          <div class="vital-bar">
            <div class="health-bar" :style="{ width: healthPerc }"></div>
          </div>
        </div>

        <div class="vital energy" v-if="hasEnergy">
          <div class="label-row">
            <div class="label">{{ energyLabel }}</div>
            <div class="amount">{{ energyCurrent }}</div>
          </div>
          <div class="vital-bar">
            <div class="energy-bar" :style="{ width: energyPerc }"></div>
          </div>
        </div>

        <div class="vital stamina">
          <div class="label-row">
            <div class="label">{{ staminaLabel }}</div>
            <div class="amount">{{ player.stamina }}</div>
          </div>
          <div class="vital-bar">
            <div class="stamina-bar" :style="{ width: staminaPerc }"></div>
          </div>
        </div>
      </div>
    </div>

    <Status />
    <div v-if="is_mobile" class="grow flex flex-col justify-center">
      <Combat @taplook="$emit('taplook')" />
    </div>
    <Abilities v-if="world.allow_combat" />
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import Status from "@/components/game/panel/Status.vue";
import Abilities from "@/components/game/panel/Abilities.vue";
import Combat from "@/components/game/panel/Combat.vue";

const store = useStore();

const is_mobile = computed(() => store.state.game.is_mobile);
const player: any = computed(() => store.state.game.player);
const world: any = computed(() => store.state.game.world);
const resourceLabels = computed(() => world.value?.labels?.resources || {});
const healthLabel = computed(() => resourceLabels.value.health || "Health");
const energyLabel = computed(() => resourceLabels.value.energy || "Energy");
const staminaLabel = computed(() => resourceLabels.value.stamina || "Stamina");
const energyCurrent = computed(() => player.value.energy ?? 0);
const energyMax = computed(() => player.value.energy_max ?? 0);
const healthPerc = computed(() => {
  const max = player.value.health_max || 1;
  return (player.value.health / max) * 100 + "%";
});
const energyPerc = computed(() => {
  const max = energyMax.value || 1;
  return (energyCurrent.value / max) * 100 + "%";
});
const staminaPerc = computed(() => {
  const max = player.value.stamina_max || 1;
  return (player.value.stamina / max) * 100 + "%";
});
const hasEnergy = computed(() => energyMax.value > 0);
</script>

<style lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.vitals-region {
  padding: 0 20px;
  .vitals,
  .effects {
    border-bottom: 1px solid $color-background-light;
    padding-bottom: 12px;
    //padding: 12px 0;
  }

  .vitals {
    display: flex;

    .vital {
      flex-grow: 1;
      width: 100%;

      &:not(:last-child) {
        margin-right: 8px;
      }

      .label {
        @include font-title-light;
        font-size: 15px;
        line-height: 18px;
        color: $color-secondary;
        margin-bottom: 2px;
      }

      .vital-bar {
        background: $color-background-very-light;
        border-radius: 6px;
        height: 6px;
        width: 100%;

        > div {
          width: 0%;
          height: 100%;
          background: $color-secondary;
          border-radius: 6px;
        }
      }
    }
  }
}

.vital {
  .label-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2px;

    .label {
      @include font-title-light;
      font-size: 15px;
      line-height: 18px;
      color: $color-secondary;
    }

    .amount {
      @include font-text-regular;
      font-size: 11px;
      line-height: 15px;
      //margin-top: 9px;
      margin-bottom: 1px;
      color: $color-text-hex-50;
    }
  }

  .vital-bar {
    background: $color-background-very-light;
    border-radius: 6px;
    height: 6px;
    width: 100%;

    > div {
      width: 0%;
      height: 100%;
      background: $color-secondary;
      border-radius: 6px;
    }
  }
}

.mobile-cast-region {
  margin: 10px 20px;
  height: 20px;

  .cast-message {
    margin: 0 auto;
    width: 33%;

    .progress-bar {
      width: 100%;
    }
  }
}
</style>
