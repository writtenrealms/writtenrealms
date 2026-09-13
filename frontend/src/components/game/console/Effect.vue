<template>
  <div class='effect' :class="{ friendly: isFriendly, hostile: isHostile}">
    <div v-if="store.state.game.player_config.combat_brief" class='brief flex'>

      <div class='effect-code grow'>
        [ {{ effectLabel }} ]
        <span v-if="targetKey === player_key">Effect Start</span>
        <span v-else>{{ targetName }} Effect Start</span>
      </div>
      <div v-if="durationLabel" class='duration whitespace-nowrap mr-2'>{{ durationLabel }}</div>
    </div>
    <div class='effect' v-else>
        {{ message.text }}
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { capfirst } from "@/core/utils.ts";

const store = useStore();
const props = defineProps<{ message: any, previousMessage: any, index: number }>();

const player_id = store.state.game.player_id;
const player_key = `player.${player_id}`;

const target = computed(() => typeof props.message.data.target === "object"
  ? props.message.data.target
  : props.message.data.target_data);
const targetKey = computed(() => target.value?.key || props.message.data.target);
const targetName = computed(() => capfirst(target.value?.keyword || target.value?.name || "Someone"));

const isFriendly = computed(() => {
  return props.message.data.friendly === true && targetKey.value === player_key;
});

const isHostile = computed(() => {
  return props.message.data.friendly === false && targetKey.value === player_key;
});

const effectLabel = computed(() => {
  const { label, name, effect, code } = props.message.data;
  return label || name || capfirst(effect || code) || "Effect";
});

const durationLabel = computed(() => {
  const { duration_rounds, duration } = props.message.data;
  if (duration_rounds != null) return `${duration_rounds} ${duration_rounds === 1 ? "rd" : "rds"}`;
  if (duration != null) return `${duration} sec`;
  return "";
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
.effect {
  &.friendly {
    color: $color-green;
    @include font-text-regular;
    .brief {
      color: $color-green !important;
    }
  }

  &.hostile {
    color: $color-red;
    @include font-text-regular;
    .brief {
      color: $color-red !important;
    }
  }
}
</style>
