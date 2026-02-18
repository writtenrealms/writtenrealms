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
import { getTargetInGroup } from "@/core/utils.ts";

const store = useStore();

const props = defineProps<{ entity: any }>();

const char = computed(() => props.entity);

const actionsMap = computed(() => {
  const actions = {
    complete: false,
    completion_action: "",
    craft: false,
    upgrade: false,
    follow: false,
    unfollow: false,
    group: false,
    list: false,
    offer: false,
    enquire: false,
    kill: false,
  };

  const sourceActions = char.value && char.value.actions;
  if (Array.isArray(sourceActions)) {
    for (const action of sourceActions) {
      actions[action] = true;
    }
  } else if (sourceActions && typeof sourceActions === "object") {
    for (const [action, value] of Object.entries(sourceActions)) {
      actions[action] = value as any;
    }
  }

  if (char.value && char.value.quest_data) {
    if (char.value.quest_data.complete) actions.complete = true;
    if (char.value.quest_data.enquire) actions.enquire = true;
  }
  if (char.value && char.value.is_merchant) {
    actions.list = true;
    actions.offer = true;
  }
  if (char.value && char.value.is_crafter) actions.craft = true;
  if (char.value && char.value.is_upgrader) actions.upgrade = true;
  if (char.value && char.value.completion_action && !actions.completion_action) {
    actions.completion_action = char.value.completion_action;
  }

  return actions;
});

const doAction = (char: any, action: string) => {
  const rawAction = String(action || "").trim();
  if (rawAction.includes(" ")) {
    store.dispatch("game/cmd", rawAction);
    store.commit("game/lookup_clear");
    store.commit("ui/modal/close");
    return;
  }
  if (action === 'craft' || action === 'upgrade') {
    store.dispatch('game/cmd', `${action}`);
  } else {
    const target = getTargetInGroup(char, store.state.game.room.chars) || char.keyword || char.name;
    store.dispatch("game/cmd", `${action} ${target}`);
  }
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
      { action: "complete", label: "COMPLETE" },
      { action: "craft", label: "CRAFT" },
      { action: "upgrade", label: "UPGRADE"},
      { action: "follow", label: "FOLLOW" },
      { action: "unfollow", label: "UNFOLLOW" },
      { action: "group", label: "GROUP" },
      { action: "list", label: "LIST" },
      { action: "offer", label: "OFFER" },
      { action: "enquire", label: "ENQUIRE" },
    ];
  const knownActionSet = new Set(actionsPriority.map(action => action.action));

  if (store.state.game.world.allow_combat) {
    actionsPriority.push({ action: "kill", label: "KILL" });
    knownActionSet.add("kill");
  }

  for (let action of actionsPriority) {
    if (actionsMap.value[action.action]) {
      // If a completion action is provided, replace the complete
      // entry with it.
      if (
        action.action == "complete" &&
        actionsMap.value.completion_action
      ) {
        var completion_action = actionsMap.value.completion_action;
        action = {
          action: completion_action.toLowerCase(),
          label: completion_action.toUpperCase()
        };
      }
      actions.push(action);
    }
    if (actions.length >= ACTIONS_COUNT) {
      break;
    }
  }

  for (const [actionCode, value] of Object.entries(actionsMap.value || {})) {
    if (actionCode === "completion_action") continue;
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
