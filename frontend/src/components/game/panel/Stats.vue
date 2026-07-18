<template>
  <div>
    <template v-if="game.is_mobile">
      <Summary />
      <img class="game-divider my-2" src="@/assets/ui/divider.svg" />
    </template>

    <div class="stats-view">
      <div class="stats-group">
        <div class="label">Attributes</div>
        <div class="stats">
          <div v-for="stat in attributeEntries" :key="stat.key" class="stat">
            <div class="st-label">{{ stat.label }}</div>
            <div class="st-value">{{ stat.value }}</div>
          </div>
        </div>
      </div>

      <div class="stats-group">
        <div class="label">Combat</div>
        <div class="stats">
          <div v-for="stat in statEntries" :key="stat.key" class="stat">
            <div class="st-label">{{ stat.label }}</div>
            <div class="st-value">{{ stat.value }}</div>
          </div>
        </div>
      </div>

      <div class="stats-group">
        <div class="label">Other</div>
        <div class="stats">
          <div class="stat">
            <div class="st-label">Exp</div>
            <div class="st-value">{{ player.experience }}</div>
          </div>

          <div
            v-for="balance in walletEntries"
            :key="`currency-${balance.currency}`"
            class="stat"
          >
            <div class="st-label">{{ balance.label }}</div>
            <div class="st-value">{{ balance.amount }}</div>
          </div>

          <div class="stat">
            <div class="st-label">Glory</div>
            <div class="st-value">{{ player.glory }}</div>
          </div>

        </div>
      </div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from 'vuex';
import Summary from "@/components/game/panel/Summary.vue";
import { walletBalanceEntries } from "@/core/economy.ts";
import { formatCombatStatValue } from "@/core/utils.ts";

const store = useStore();

const player = computed(() => store.state.game.player);
const game = computed(() => store.state.game);
const world = computed(() => store.state.game.world);
const walletEntries = computed(() => walletBalanceEntries(
  world.value?.economy,
  player.value?.economy,
));

const attributeLabels = computed(() => world.value?.labels?.attributes || {});
const attributeOrder = computed(() => world.value?.labels?.order?.attributes || Object.keys(player.value?.attributes || {}));
const statLabels = computed(() => world.value?.labels?.stats || {});
const statOrder = computed(() => world.value?.labels?.order?.stats || Object.keys(player.value?.stats || {}));

const attributeEntries = computed(() => {
  const values = player.value?.attributes || {};
  return attributeOrder.value
    .filter((key: string) => values[key] !== undefined)
    .map((key: string) => ({
      key,
      label: attributeLabels.value[key] || key.replace(/_/g, " "),
      value: values[key],
    }));
});

const formatStatValue = (key: string, value: number) => {
  return formatCombatStatValue(world.value, player.value, key, value, "paren");
};

const statEntries = computed(() => {
  const values = player.value?.stats || {};
  return statOrder.value
    .filter((key: string) => values[key] !== undefined)
    .map((key: string) => ({
      key,
      label: statLabels.value[key] || key.replace(/_/g, " "),
      value: formatStatValue(key, values[key]),
    }));
});
</script>

<style lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.stats-view {
  padding: 0 20px;
  .stats {
    display: flex;
    flex-wrap: wrap;
    .stat {
      flex: 1 1 50%;
      display: flex;
      justify-content: space-between;

      &:nth-child(odd) {
        padding-right: 5px;
      }
      &:nth-child(even) {
        padding-left: 5px;
      }

      .st-label {
        color: $color-text-hex-50;
      }
    }
  }

  .stats-group {
    &:not(:first-child) {
      margin-top: 12px;
    }

    .label {
      @include font-title-light;
      color: $color-secondary;
      font-size: 15px;
      line-height: 18px;
    }
  }
}
</style>
