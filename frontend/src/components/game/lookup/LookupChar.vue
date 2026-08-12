<template>
  <div class="lookup-desktop lookup-char">
    <CharInfo :char="char" />

    <div class="actions" v-if="!isSelf">
      <div
        class="action primary"
        v-if="actionsData.primaryAction"
        @click="doAction(char, actionsData.primaryAction)"
      >{{ actionsData.primaryAction.label }}</div>
      <div
        class="action"
        v-for="(action, index) in actionsData.actions"
        :key="index"
        @click="doAction(char, action)"
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

const doAction = (char: any, action: any) => {
  const actionCode = String(action?.action || action || "").trim();
  const rawAction = String(action?.command || actionCode).trim();
  if (action?.exact || rawAction.includes(" ")) {
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

  const actions: any[] = [];
  const hasTrainingActions = Boolean(
    actionsMap.value.learn || actionsMap.value.unlearn
  );
  const hasMerchantActions = Boolean(
    actionsMap.value.list || actionsMap.value.offer
  );
  // Keep ordinary lookups compact, while ensuring a mob that provides both
  // services does not hide either API behind the three-action limit.
  const ACTIONS_COUNT = hasTrainingActions && hasMerchantActions ? 5 : 3;
  const actionsPriority = [
      // higher the better
      { action: "learn", label: "LEARN", exact: true },
      { action: "unlearn", label: "UNLEARN", exact: true },
      { action: "list", label: "LIST", exact: false },
      { action: "offer", label: "OFFER", exact: false },
      { action: "talk", label: "TALK" },
      { action: "follow", label: "FOLLOW" },
      { action: "unfollow", label: "UNFOLLOW" },
      { action: "group", label: "GROUP" },
    ];
  const knownActionSet = new Set(actionsPriority.map(action => action.action));

  if (store.state.game.world && store.state.game.world.allow_combat) {
    actionsPriority.push({ action: "kill", label: "KILL", exact: false });
    knownActionSet.add("kill");
  }

  for (let action of actionsPriority) {
    if (actionsMap.value[action.action]) {
      const authored = actionsMap.value[action.action];
      actions.push({
        ...action,
        label: typeof authored === "object" && authored?.label
          ? String(authored.label)
          : action.label,
        command: typeof authored === "string"
          ? authored
          : typeof authored === "object" && authored?.command
            ? String(authored.command)
            : action.action,
        exact: typeof authored === "object" && typeof authored?.exact === "boolean"
          ? authored.exact
          : action.exact,
      });
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
      label: typeof value === "object" && value?.label
        ? String(value.label)
        : actionCode.toUpperCase(),
      command: typeof value === "string"
        ? value
        : typeof value === "object" && value?.command
          ? String(value.command)
          : actionCode,
      exact: typeof value === "object" && value?.exact === true,
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
