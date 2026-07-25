<template>
  <div>
    <span
      v-if="!isLastMessage || !isCurrentRoomItem || !corpse.inventory.length"
    >{{ message.text }}</span>
    <span
      class="color-secondary interactive"
      v-interactive="{
        target: corpse,
        primaryAction: true,
        actionContext: 'room',
      }"
      v-else
    >{{ message.text }}</span>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";

const store = useStore();

const props = defineProps<{message: any}>();

const corpse = computed(() => props.message.data.corpse);
const isCurrentRoomItem = computed(() => (
  (store.state.game.room?.inventory || [])
    .some((item: any) => item.key === corpse.value.key)
));
const isLastMessage = computed(
  () => store.state.game.last_message[props.message.type] === props.message,
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
