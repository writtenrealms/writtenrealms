<template>
  <div class="look-char indented">
    <CharInfo :char="message.data.target" :isLastMessage="isLastMessage" :message="message" />
    <div v-if="isLastMessage && isInRoom && hasAction" class='mt-4'>
      <button
        class="btn-small mr-2"
        v-if="targetActions.enquire"
        @click="doAction(message.data.target, 'enquire')">ENQUIRE</button>
      <button
        class="btn-small mr-2"
        v-if="targetActions.completion_action"
        @click="doAction(message.data.target, targetActions.completion_action)"
      >{{ targetActions.completion_action.toUpperCase() }}</button>
      <button
        class="btn-small mr-2"
        v-else-if="targetActions.complete"
        @click="doAction(message.data.target, 'complete')">COMPLETE QUEST</button>
      <button
        class="btn-small mr-2"
        v-if="targetActions.list"
        @click="doAction(message.data.target, 'list')">LIST</button>
      <button
        class="btn-small mr-2"
        v-if="targetActions.offer"
        @click="doAction(message.data.target, 'offer')">OFFER</button>
      <button
        class="btn-small mr-2"
        v-if="targetActions.craft"
        @click="doAction(message.data.target, 'craft')">CRAFT</button>
      <button
        class="btn-small mr-2"
        v-if="targetActions.follow"
        @click="doAction(message.data.target, 'follow')">FOLLOW</button>
      <button
        class="btn-small mr-2"
        v-if="targetActions.unfollow"
        @click="doAction(message.data.target, 'unfollow')">UNFOLLOW</button>
      <button
        class="btn-small mr-2"
        v-for="action in extraActions"
        :key="action"
        @click="doAction(message.data.target, action)"
      >{{ action.toUpperCase() }}</button>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed, } from "vue";
import { useStore } from "vuex";
import CharInfo from "@/components/game/console/CharInfo.vue"
import { getTargetInGroup } from "@/core/utils.ts";

const store = useStore();
const KNOWN_ACTIONS = new Set([
  "enquire",
  "completion_action",
  "complete",
  "list",
  "offer",
  "craft",
  "follow",
  "unfollow",
]);

const props = defineProps<{
  message: any;
}>();

const targetActions = computed(() => {
  const actions = {
    enquire: false,
    completion_action: "",
    complete: false,
    list: false,
    offer: false,
    craft: false,
    follow: false,
    unfollow: false,
  };

  const sourceActions = props.message.data.target.actions;
  if (Array.isArray(sourceActions)) {
    for (const action of sourceActions) {
      actions[action] = true;
    }
  } else if (sourceActions && typeof sourceActions === "object") {
    for (const [action, value] of Object.entries(sourceActions)) {
      actions[action] = value as any;
    }
  }

  if (props.message.data.target.quest_data) {
    if (props.message.data.target.quest_data.complete) actions.complete = true;
    if (props.message.data.target.quest_data.enquire) actions.enquire = true;
  }

  if (props.message.data.target.is_merchant) {
    actions.list = true;
    actions.offer = true;
  }
  if (props.message.data.target.is_crafter) actions.craft = true;

  if (props.message.data.target.completion_action && !actions.completion_action) {
    actions.completion_action = props.message.data.target.completion_action;
  }

  return actions;
});

const hasAction = computed(() => {
  const hasKnownAction = Boolean(
    targetActions.value.complete ||
    targetActions.value.completion_action ||
    targetActions.value.list ||
    targetActions.value.offer ||
    targetActions.value.follow ||
    targetActions.value.unfollow ||
    targetActions.value.enquire
  );
  if (hasKnownAction) return true;
  return Object.entries(targetActions.value || {}).some(([actionCode, value]) => {
    if (KNOWN_ACTIONS.has(actionCode)) return false;
    return Boolean(value);
  });
});

const doAction = (char, action) => {
  if (String(action || "").includes(" ")) {
    store.dispatch("game/cmd", String(action));
    store.commit("game/lookup_clear");
    store.commit("ui/modal_clear");
    return;
  }
  const target = getTargetInGroup(char, store.state.game.room.chars) || char.keyword || char.name;
  if (action === 'follow' || action === 'unfollow') {
    store.dispatch("game/cmd", `${action} ${target}`)
  } else if (target && target.indexOf(".") === -1)
    store.dispatch("game/cmd", `${action}`);
  else store.dispatch("game/cmd", `${action} ${target}`);

  store.commit("game/lookup_clear");
  store.commit("ui/modal_clear");
}

const extraActions = computed(() => {
  const actions: string[] = [];
  for (const [actionCode, value] of Object.entries(targetActions.value || {})) {
    if (KNOWN_ACTIONS.has(actionCode)) continue;
    if (!value) continue;
    actions.push(actionCode);
  }
  return actions;
});

const isLastMessage = computed(() => {
  return (
    store.state.game.last_message[props.message.type] == props.message
  );
});

const isInRoom = computed(() => {
    return store.state.game.room.key === props.message.data.actor.room.key;
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
