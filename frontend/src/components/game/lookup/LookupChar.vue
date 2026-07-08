<template>
  <div class="lookup-desktop lookup-char">
    <CharInfo :char="char" />

    <div class="actions" v-if="!isSelf">
      <div
        class="action primary"
        v-if="actionsData.primaryAction"
        @click="doAction(char, actionsData.primaryAction.action)"
      >{{ actionsData.primaryAction.label }}</div>
      <div
        class="action"
        v-for="(action, index) in actionsData.actions"
        :key="index"
        @click="doAction(char, action.action)"
      >{{ action.label}}</div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import CharInfo from "@/components/game/CharInfo.vue";
import { buildCharActions } from "@/core/charActions";
import { getTargetInGroup } from "@/core/utils.ts";

const store = useStore();

const props = defineProps<{ entity: any }>();

const char = computed(() => props.entity);

const actionsMap = computed(() =>
  buildCharActions(char.value, store.state.game.player, store.state.game.world)
);

const doAction = (char: any, action: string) => {
  const rawAction = String(action || "").trim();
  if (rawAction.includes(" ")) {
    store.dispatch("game/cmd", rawAction);
    store.commit("game/lookup_clear");
    store.commit("ui/modal/close");
    return;
  }
  const target = getTargetInGroup(char, store.state.game.room.chars) || char.keyword || char.name;
  store.dispatch("game/cmd", `${rawAction} ${target}`);
  store.commit("game/lookup_clear");
  store.commit("ui/modal/close");
};

const actionsData = computed(() => {
  if (!actionsMap.value) {
    return {};
  }

  let actions: any[] = [],
    ACTIONS_COUNT = 3,
    actionsPriority = [
      // higher the better
      { action: "talk", label: "TALK" },
      { action: "follow", label: "FOLLOW" },
      { action: "unfollow", label: "UNFOLLOW" },
      { action: "group", label: "GROUP" },
      { action: "list", label: "LIST" },
      { action: "offer", label: "OFFER" },
    ];
  const knownActionSet = new Set(actionsPriority.map(action => action.action));

  if (store.state.game.world && store.state.game.world.allow_combat) {
    actionsPriority.push({ action: "kill", label: "KILL" });
    knownActionSet.add("kill");
  }

  for (let action of actionsPriority) {
    if (actionsMap.value[action.action]) {
      actions.push(action);
    }
    if (actions.length >= ACTIONS_COUNT) {
      break;
    }
  }

  for (const [actionCode, value] of Object.entries(actionsMap.value || {})) {
    if (knownActionSet.has(actionCode)) continue;
    if (!value) continue;
    actions.push({
      action: actionCode,
      label: actionCode.toUpperCase(),
    });
    if (actions.length >= ACTIONS_COUNT) {
      break;
    }
  }

  var primaryAction;
  if (actions.length > 0) {
    primaryAction = actions.shift();
  }

  return {
    primaryAction: primaryAction,
    actions: actions,
    displayActions: true
  };

});

const isSelf = computed(() => {
  if (char.value.key === store.state.game.player.key) return true;
  return false;
});

</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
