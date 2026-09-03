<template>
  <div v-if="player_target">
    <div class="combat-view">
      <div class="combat-header">
        <span v-if="player_target.health">You're in a fight</span>
        <span v-else>{{ capfirst(player_target.name) }} is dead! R.I.P.</span>
      </div>
      <div class="combat-box">
        <div class="target-name">{{ player_target.name }}</div>
        <div class="target-summary color-text-50">Level {{ player_target.level }}</div>
        <div class="target-info">
          <div class="target-health-region">
            <div class="target-health">
              <div class="vital health">
                <div class="label-row">
                  <div class="label">Health</div>
                  <div class="amount">{{ player_target.health }}</div>
                </div>
                <div class="vital-bar">
                  <div class="health-bar" :style="{width: healthPerc + '%'}"></div>
                </div>
              </div>
            </div>
          </div>


            <div v-if="target_round_effects.length" class="target-round-effects">
              <div
                v-for="effect in target_round_effects"
                :key="effect.key"
                class="target-round-effect"
                :class="[`effect-${effect.category}`]"
                :title="effect.title"
                :aria-label="effect.title"
                :style="{ '--effect-fill': effect.fillWidth }"
              >
                <span class="target-round-effect-fill"></span>
                <span class="target-round-effect-label">{{ effect.label }}</span>
              </div>
            </div>

            <div v-if="target_effects.length" class="target-effects">
              <ProgressBar
                v-for="effect in target_effects"
                :key="effect.expires"
                :duration="effect.duration"
                :label="effect.code"
                :expires="effect.expires"
                :start="effect.start"
                method="channel"
              />
            </div>


        </div>
        <div class="actions">
          <div
            class="action primary"
            action="flee"
            v-if="player_target.health"
            @click="doAction('flee')"
          >FLEE</div>
          <div class="action primary" action="loot" v-else @click="doAction('loot')">LOOT</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed, ref, watch, onUnmounted } from "vue";
import { useStore } from "vuex";
import { capfirst, resourcePercent } from "@/core/utils";
import { presentRoundEffects } from "@/core/roundEffects";
import ProgressBar from "@/components/game/ProgressBar.vue";

const store = useStore();

const timeout: any = ref(null);

const emit = defineEmits(["taplook"]);

const player_target = computed(() => {
  // For testing
  // return {
  //   health: 100,
  //   health_max: 100,
  //   name: 'a dummy',
  //   level: 10,
  // }
  return store.state.game.player_target;
});

const doAction = (action) => {
  store.dispatch("game/cmd", action);
  if (action === "flee" || action === "loot") {
    emit("taplook");
  }
}

watch(player_target, (player_target) => {
  if (player_target && player_target.health === 0) {
    if (!store.state.game.is_mobile) {
      timeout.value = setTimeout(() => {
        if (player_target && player_target.health === 0) {
          store.commit("game/player_target_set", null);
        }
      }, 5000);
    }
  }
});

  /*
  @Watch("player_target")
  onPlayerTargetChange(player_target) {
    if (player_target && player_target.health === 0) {
      if (!this.$store.state.game.is_mobile) {
        this.timeout = setTimeout(() => {
          if (this.player_target && this.player_target.health === 0) {
            this.$store.commit("game/player_target_set", null);
          }
        }, 5000);
      }
    }
  }
  */

onUnmounted(() => {
  if (timeout.value) {
    clearTimeout(timeout.value);
  }
});

const healthPerc = computed(() => {
  return resourcePercent(player_target.value?.health, player_target.value?.health_max);
});

const target_round_effects = computed(() => {
  if (!player_target.value || Number(player_target.value.health || 0) <= 0) {
    return [];
  }
  return presentRoundEffects(player_target.value.active_effects, {
    useStatusLabels: true,
  });
});

const target_effects = computed(() => {
  // For testing
  // return [
  //   {
  //     active: true,
  //     actor: "player.128",
  //     code: "wrack",
  //     duration: 18,
  //     expires: 1565625865.473659,
  //     key: "1565625865.473659wrack",
  //     metadata: null,
  //     stack: 1,
  //     target: "mob.559776",
  //     value: 0
  //   },
  //   {
  //     active: true,
  //     actor: "player.128",
  //     code: "wrack",
  //     duration: 18,
  //     expires: 1565625865.473659,
  //     key: "1565625865.473659wrack",
  //     metadata: null,
  //     stack: 1,
  //     target: "mob.559776",
  //     value: 0
  //   },
  //   {
  //     active: true,
  //     actor: "player.128",
  //     code: "wrack",
  //     duration: 18,
  //     expires: 1565625865.473659,
  //     key: "1565625865.473659wrack",
  //     metadata: null,
  //     stack: 1,
  //     target: "mob.559776",
  //     value: 0
  //   }
  // ];
  if (!player_target.value?.key) return [];
  return store.state.game.effects[player_target.value.key] || [];
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.combat-view {
  width: 285px;
  z-index: 1000;

  .combat-header {
    background: $color-red;
    height: 31px;

    color: $color-text;
    @include font-text-regular;
    font-size: 12px;
    line-height: 26px;

    display: flex;
    align-items: center;
    padding-left: 10px;
  }

  .combat-box {
    background: $color-background-light;
    border: 3px solid #333333;
    border-top: 0;
    padding: 10px;
    line-height: 21px;

    .target-name {
      @include font-text-regular;
      color: $color-red;
    }

    .target-info {
      display: flex;
      flex-wrap: nowrap;
      justify-content: space-between;
      align-items: center;

      .target-health {
        width: 100px;
        margin-top: 10px;
      }

      .effect {
        margin-top: 10px;
      }

      .target-effects {
        display: flex;
        flex-grow: 1;
        flex-direction: row-reverse;
        flex-wrap: wrap;
        // justify-content: center;

        .progress-bar {
          margin: 1px 2px;
        }
      }

      .target-round-effects {
        display: flex;
        flex-grow: 1;
        flex-direction: row-reverse;
        flex-wrap: wrap;
        gap: 4px;
        margin-left: 8px;
      }

      .target-round-effect {
        @include font-text-regular;
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 20px;
        padding: 1px 8px;
        border: 1px solid $color-red;
        border-radius: 8px;
        background: $color-red-70;
        color: $color-text;
        font-size: 12px;
        line-height: 16px;
        overflow: hidden;
        white-space: nowrap;
      }

      .target-round-effect.effect-buff {
        border-color: $color-green;
        background: $color-green-dark;
      }

      .target-round-effect-fill {
        position: absolute;
        inset: 0 auto 0 0;
        width: var(--effect-fill);
        background: $color-red;
      }

      .target-round-effect.effect-buff .target-round-effect-fill {
        background: $color-green;
      }

      .target-round-effect-label {
        position: relative;
        z-index: 1;
        overflow: hidden;
        text-overflow: ellipsis;
      }
    }

    .actions {
      margin-top: 10px;
    }
  }
}
</style>
