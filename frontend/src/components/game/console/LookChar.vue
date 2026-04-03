<template>
  <div class="look-char indented">
    <CharInfo :char="message.data.target" :isLastMessage="isLastMessage" :message="message" />
    <div v-if="isLastMessage && isInRoom && hasAction" class='mt-4'>
      <button
        class="btn-small mr-2"
        v-if="targetActions.talk"
        @click="doAction(message.data.target, 'talk')">TALK</button>
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
import { buildCharActions } from "@/core/charActions";
import { getTargetInGroup } from "@/core/utils.ts";

const store = useStore();
const KNOWN_ACTIONS = new Set([
  "talk",
  "list",
  "offer",
  "craft",
  "follow",
  "unfollow",
]);

const props = defineProps<{
  message: any;
}>();

const targetActions = computed(() =>
  buildCharActions(
    props.message.data.target,
    store.state.game.player,
    store.state.game.world
  )
);

const hasAction = computed(() => {
  const hasKnownAction = Boolean(
    targetActions.value.talk ||
    targetActions.value.craft ||
    targetActions.value.list ||
    targetActions.value.offer ||
    targetActions.value.follow ||
    targetActions.value.unfollow
  );
  if (hasKnownAction) return true;
  return Object.entries(targetActions.value || {}).some(([actionCode, value]) => {
    if (KNOWN_ACTIONS.has(actionCode)) return false;
    return Boolean(value);
  });
});

const doAction = (char, action) => {
  const rawAction = String(action || "").trim();
  if (rawAction.includes(" ")) {
    store.dispatch("game/cmd", rawAction);
    store.commit("game/lookup_clear");
    store.commit("ui/modal_clear");
    return;
  }
  const target = getTargetInGroup(char, store.state.game.room.chars) || char.keyword || char.name;
  if (rawAction === 'craft' || rawAction === 'upgrade') {
    store.dispatch("game/cmd", rawAction);
  } else {
    store.dispatch("game/cmd", `${rawAction} ${target}`);
  }

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
