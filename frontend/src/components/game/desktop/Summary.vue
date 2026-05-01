<template>
  <div class="summary-region">
    <div>
      <div class="name-and-summary">
        <div class="name">{{ player.name }}</div>
        <div class="summary"><span v-if="world.allow_combat">{{ combatSummary }}</span><span v-else>&nbsp;</span></div>
      </div>
      <div class="experience">
        <div class="exp-gained" :style="{width: expPerc +'%'}" :title="expProgress"></div>
      </div>
    </div>
  </div>
</template>

<script type='ts' setup>
import { computed } from "vue";
import { useStore } from 'vuex';
import { capfirst } from "@/core/utils";

const store = useStore();

const world = computed(() => store.state.game.world);
const player = computed(() => store.state.game.player);

const classLabel = computed(() => {
  const archetype = String(player.value?.archetype || "").trim();
  if (!archetype || world.value?.is_classless) return "";
  const labels = world.value?.labels?.classes || {};
  return labels[archetype] || capfirst(archetype);
});

const combatSummary = computed(() => {
  const levelText = `level ${player.value?.level || 1}`;
  if (!classLabel.value) return capfirst(levelText);
  return `${classLabel.value} ${levelText}`;
});

const expPerc = computed(() => {
  const needed = player.value.experience_needed,
    progress = player.value.experience_progress;
  return (progress / (needed + progress)) * 100;
});

const expProgress = computed(() => 'Experience: ' + expPerc.value + '%');
</script>

<style lang='scss'>
@import "@/styles/colors.scss";

.summary-region {
  padding: 5px 20px 15px 20px;

  .name-and-summary {
    display: flex;

    .name {
      font-size: 14px;
      line-height: 19px;
      flex-grow: 1;
    }

    .summary {
      color: $color-text-hex-50;
    }
  }

  .experience {
    background: #333;
    border-radius: 3px;
    height: 3px;
    width: 100%;

    .exp-gained {
      background: $color-secondary;
      height: 100%;
      width: 0%;
      border-radius: 3px;
    }
  }
}
</style>
