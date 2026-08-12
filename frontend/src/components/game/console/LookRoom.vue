<template>
  <div class="look-room">

    <div v-if="isStateSnapshot && motd_lines.length" class="motd-wrapper mb-4">
      <div class="motd">
        <div class="motd-header mb-4">Message of the Day</div>
        <div v-for="(line, index) in motd_lines" :key="index" v-html="line" class="motd-line"></div>
      </div>
    </div>

    <div v-if="isStateSnapshot && message.data.world.instance_ref" class="mb-4">
      Instance ID: {{ message.data.world.instance_ref }}
    </div>

    <div v-if="message.type === 'affect.death'" class="mt-4 color-text-red font-text-regular">
      {{ message.text || "You have been slain! Rest your weary bones..." }}
    </div>

    <div v-if="penaltyDisplay" class='font-text-regular color-text-red'>
      {{ penaltyDisplay }}
    </div>

    <div v-if="isFleeSuccess" class="mb-4">
      <span v-if="message.data.is_auto">Your health is too low and you flee {{ message.data.direction }}!</span>
      <span v-else>You flee {{ message.data.direction }}!</span>
    </div>

    <div>
      <span class='room-name'>{{ room.name }}</span>
      <span
        class='room-id ml-2 color-text-50'
        v-if="player.is_builder && room.manifest_ref"
      >[ {{ room.manifest_ref }} ]</span>
    </div>

    <!-- Description -->
    <template v-if="showDescription">
      <template v-if="isLastMessage">
        <div class="room-description" v-for="(line, index) in descLines" :key="index">
          <template v-for="(word, word_index) in line.split(' ')">
            <span :key="word_index" v-if="isDetail(word)" class="interactive" @click="onClickDetail(word)">{{ word
              }}</span>
            <span :key="word_index + 'else'" v-else>{{ word }}</span>
            {{ " " }}
          </template>
        </div>
      </template>
      <template v-else>
        <div class="room-description" v-for="(line, index) in descLines" :key="index">
          {{ line }}
        </div>
      </template>
    </template>

    <div class="room-exits" v-if="exits">{{ exits }}</div>

    <template v-if="room.houses">
      <div v-for="house in room.houses" :key="house.key">
        {{ house.name }}'s house lies
        <template v-if="house.direction == 'up'">above.</template>
        <template v-else-if="house.direction == 'down'">below.</template>
        <template v-else>to the {{ house.direction }}.</template>
      </div>
    </template>

    <div class="room-inventory">
      <div class="room-item" v-for="item in inventoryStack" :key="item.key">
        <template v-if="isLastMessage && isCurrentRoomItem(item)">
          <span
            v-interactive="{
              target: item,
              primaryAction: true,
              actionContext: 'room',
            }"
            class="interactive"
            :class="[item.quality]"
          >
            {{ itemRoomDescription(item) }}
            <span v-if="item.indicator" class="quest-indicator-wrapper">[ <span class="quest-indicator">{{ item.indicator }}</span> ]</span>
            <template v-if="item.count && item.count > 1">[{{ item.count }}]</template>
          </span>
        </template>
        <template v-else>
          <span>
            {{ itemRoomDescription(item) }}
            <span v-if="item.indicator" class="quest-indicator-wrapper">[ <span class="quest-indicator">{{ item.indicator }}</span> ]</span>
            <template v-if="item.count && item.count > 1">[{{ item.count }}]</template>
          </span>
        </template>
      </div>
    </div>

    <div class="room-chars">
      <div class="room-char" v-for="char in chars" :key="char.key + '_' + message.message_id">
        <LookRoomChar
            :isLastMessage="isLastMessage"
            :char="char"
            :key="char.key + message.message_id"
            @click="onCharClick(char)"/>
      </div>
    </div>

    <div class="room-quest-callouts" v-if="room.quest_callouts && room.quest_callouts.length">
      <div class="room-quest-callout" v-for="callout in room.quest_callouts" :key="callout.slug + '_' + callout.text">
        <template v-if="isLastMessage">
          <span class="interactive" @click="onInspectCallout(callout)">{{ callout.text }}</span>
        </template>
        <template v-else>
          <span>{{ callout.text }}</span>
        </template>
        <span class="quest-indicator-wrapper">[ <span class="quest-indicator">{{ callout.indicator || "!" }}</span> ]</span>
      </div>
    </div>

    <div class="room-actions mt-2" v-if="isLastMessage && roomActions.length">
      <button class='btn-small mr-2' v-for="(action, index) in roomActions" :key="index"
        @click="onClickRoomAction(action)">{{ action.toUpperCase() }}</button>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref, watch } from "vue";
import { useStore } from "vuex";
import { DIRECTIONS } from "@/constants";
import { stackedInventory, getTargetInGroup } from "@/core/utils";
import LookRoomChar from "@/components/game/console/LookRoomChar.vue";
import { formatMoney } from "@/core/economy.ts";
import { parseLinks } from "@/core/utils";
import {
  getRoomMerchantProvider,
  merchantProviderTarget,
  roomActionsForMerchantProvider,
} from "@/core/merchantProviders";
import { roomActionsForTrainingProvider } from "@/core/trainingProviders";

const store = useStore();

interface Char {
  key: string;
  name: string;
  id: number;
  is_invisible: boolean;
}

interface Room {
  id: number;
  manifest_ref?: string;
  name: string;
  description: string;
  chars: Char[];
  inventory: {}[];
  details: string[];
  actions: string[];
  houses: any[];
  quest_callouts: any[];
}

const props = defineProps<{ message: any }>();
const room = ref<Room>(props.message.data.room);
const roomMerchantProvider = computed(() => getRoomMerchantProvider(room.value));
const roomActions = computed(() => roomActionsForTrainingProvider(
  room.value,
  roomActionsForMerchantProvider(room.value),
));
const penaltyDisplay = computed(() => {
  const penaltyText = String(props.message?.data?.penalty_text || "").trim();
  if (penaltyText) return penaltyText;
  const penalty = props.message?.data?.penalty;
  return penalty
    ? `You lose ${formatMoney(penalty, store.state.game.world?.economy)}.`
    : "";
});

// Room Inventory Management
const roomInventory = computed(() => {
  if (room.value) return room.value.inventory;
  return []
})
const inventoryStack = ref<any[]>([]);
const updateInventoryStack = () => {
  inventoryStack.value = stackedInventory(roomInventory.value);
}
watch (roomInventory, () => {
  updateInventoryStack();
}, { immediate: true });

if (props.message.type === "cmd.look.success" || props.message.type === "cmd./jump.success") {
  room.value = props.message.data.target;
} else {
  room.value = props.message.data.room;
}

const isLastMessage = computed(() => {
  return props.message === store.state.game.last_viewed_room_message;
});
const currentRoomItemKeys = computed(() => new Set(
  (store.state.game.room?.inventory || []).map((item: any) => item.key),
));
const isCurrentRoomItem = (item: any) => (
  currentRoomItemKeys.value.has(item.key)
);

const isStateSnapshot = computed(() => {
  if (props.message.type === "cmd.state.sync.success") {
    return true;
  }
  return (
    props.message.type === "system.connect.success" &&
    props.message.data &&
    props.message.data.room
  );
});

const isFleeSuccess = computed(() => {
  return props.message.type === "affect.flee.success" || props.message.type === "cmd.flee.success";
});

const descLines = computed(() => {
  if (!room.value.description) return [];
  return room.value.description.split("\n");
});


const isDetail = (word) => {
  if (!room || !room.value.details) return false;
  const trimmedWord = word.split(/\W+/)[0];
  for (const detail of room.value.details) {
    if (trimmedWord.toLowerCase() === detail.toLowerCase()) return true;
  }
  return false;
}

const exits = computed(() => {
  let exits: string[] = [];
  for (const dir in DIRECTIONS) {
    if (room.value[dir]) {
      exits.push(dir[0].toUpperCase());
    }
  }
  if (exits.length) {
    return `[ exits: ${exits.join(" ")} ]`;
  }
  return "";
});

const chars = computed(() => {
  let chars: Char[] = [];
  const playerKey = `player.${store.state.game.player_id}`;
  for (let char of room.value.chars) {
    if (char.key !== playerKey) {
      chars.push(char);
    }
  }
  return chars;
});

const player = computed(() => store.state.game.player);
const is_mobile = computed(() => store.state.game.is_mobile);

const itemRoomDescription = (item) => {
  if (item && item.room_description) {
    return item.room_description;
  }
  const name = item && item.name ? item.name : "item";
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} lies here.`;
};

const onCharClick = (char) => {
  if (is_mobile.value || !isLastMessage.value) return;
  const target = getTargetInGroup(char, room.value.chars, player.value);
  store.dispatch("game/cmd", `look ${target}`);
}

const onClickDetail = (word) => {
  const trimmedWord = word.split(/\W+/)[0];
  store.dispatch("game/cmd", `l ${trimmedWord}`);
}

const onClickRoomAction = (action) => {
  const normalizedAction = String(action || "").trim();
  const directMerchantAction = normalizedAction.toLowerCase();
  if (
    roomMerchantProvider.value?.type === "room"
    && ["list", "offer"].includes(directMerchantAction)
  ) {
    const merchantTarget = merchantProviderTarget(roomMerchantProvider.value);
    store.dispatch(
      "game/cmd",
      merchantTarget
        ? `${directMerchantAction} ${merchantTarget}`
        : directMerchantAction,
    );
    return;
  }
  store.dispatch("game/cmd", normalizedAction);
}

const onInspectCallout = (callout) => {
  const command = callout && callout.command ? callout.command : "inspect";
  store.dispatch("game/cmd", command);
}

const showDescription = computed(() => {
  if (
    store.state.game.player_config.room_brief &&
    props.message.type !== "cmd.look.success"
  ) {
    return false;
  }
  return true;
});

const motd_lines = computed(() => {
  let lines = [];

  if (!store.state.game.motd) return [];

  if (store.state.game.motd) {
    lines = store.state.game.motd.split("\n").map(l => parseLinks(l));
  }

  return lines || "&nbsp;"

  return store.state.game.motd.split("\n").map(l => parseLinks(l));
  // return store.state.game.motd.split('\n') || [];
});


</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.look-room {
  .room-name {
    @include font-text-regular;
    color: $color-secondary;
    line-height: 21px;
  }

  .room-description {
    line-height: 21px;
    min-height: 14px;
    color: #ddd;
  }

  .room-description:empty:before {
    content: "\200b";
  }

  .flee-direction {
    margin-bottom: 1em;
  }

  .room-note {
    opacity: 0.5;
  }

  .quest-indicator-wrapper {
    color: $color-text-gray;
  }

  .quest-indicator {
    color: $color-secondary;
    font-weight: bold;
  }

  .room-quest-callout {
    display: flex;
    gap: 10px;
    align-items: baseline;
  }
}

.motd {
  border: 1px solid $color-background-light-border;
  padding: 1rem;

  .motd-header {
    color: $color-primary;
    font-weight: bold;
  }

  .motd-line {
    :deep(a) {
      color: $color-text-70;

      &:hover {
        color: $color-text;
        text-decoration: none;
        border-bottom-color: #aaa;
        cursor: pointer;
      }
    }
  }
}
</style>
