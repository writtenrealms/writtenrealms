<template>
  <div class="lookup-desktop lookup-item">
    <ItemInfo :item="item" :from_lookup="true" />

    <div class="actions">
      <div
        class="action primary"
        @click="doAction(item, actions.primaryAction.action)"
        v-if="actions.primaryAction"
      >{{ actions.primaryAction.label }}</div>
      <div
        class="action"
        v-for="action in actions.actions"
        :key="actions.actions.indexOf(action)"
        @click="doAction(item, action.action)"
      >{{ action.label }}</div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import ItemInfo from "@/components/game/ItemInfo.vue";
import {
  buildItemActionChoices,
  executeItemAction,
} from "@/core/itemActions";

const store = useStore();

const props = defineProps({
  entity: {
    type: Object,
    required: true
  }
});

const item = computed(() => props.entity);
const actions = computed(() => (
  buildItemActionChoices(item.value, store.state.game)
));

const doAction = (target, action) => {
  executeItemAction(store, target, action);
};
</script>
