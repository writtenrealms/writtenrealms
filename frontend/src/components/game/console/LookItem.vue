<template>
  <div class="look-item indented">
    <ItemInfo
      :item="message.data.target"
      :contents-interactive="isLastMessage"
    />
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import ItemInfo from "../ItemInfo.vue";

const store = useStore();
const props = defineProps<{
  message: any;
}>();

const isLastMessage = computed(
  () => store.state.game.last_message[props.message.type] === props.message,
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
