<template>
  <div id="sidebar">
    <!-- Quest Log -->
    <div class="sidebar-element logs">
      <h3>LOGS</h3>
      <div class="mt-4">
        <button class="btn-small mb-1 button-gray" @click="onClickQuestLog">QUEST LOG</button>
        <button class="btn-small button-gray" @click="onClickCommunicationLog" v-if="world.is_multiplayer">COMMUNICATION LOG</button>
      </div>
    </div>

    <!-- Focus -->
    <div class="sidebar-element focus" v-if="allow_combat">
      <h3>
        FOCUS
        <Help :help="focus_help" />
      </h3>
      <Focus class="mt-2" />
    </div>

    <!-- Who list -->
    <div class="sidebar-element who-list" v-if="world.is_multiplayer">
      <h3 v-if="who_list" @click="onClickExpand('who')" class="hover">
        <span v-if="expanded === 'who'">-</span>
        <span v-else>+</span>
        {{ who_list.length }}
        <template
          v-if="who_list.length == 1"
        >player</template>
        <template v-else>players</template>
        in world
      </h3>
      <div v-if="expanded === 'who'" class="who-list-detail">
        <div
          v-for="player in who_list"
          :key="player.key"
          class='hover'
          :class="{ 'color-secondary': player.name_recognition, 'color-primary': player.is_immortal }"
          @click="onClickWhoPlayer(player)"
        >
          {{ player.name }} {{ player.title }}
          <span v-if="player.is_idle" class='ml-1 color-text-50'>(Idle)</span>

        </div>
      </div>
    </div>

    <!-- Chars -->
    <div class="sidebar-element chars">
      <h3 @click="onClickExpand('chars')" class="hover">
        <span v-if="expanded === 'chars'">-</span>
        <span v-else>+</span>
        {{ room_chars_length }}
        <template
          v-if="room_chars_length == 1"
        >CHARACTER</template>
        <template v-else>CHARACTERS</template>
        IN ROOM
      </h3>
      <div v-if="expanded === 'chars'" class="my-1">
        <Chars/>
      </div>
    </div>

    <!-- News -->
  </div>
</template>

<script lang="ts" setup>
import { ref, computed } from "vue";
import { useStore } from "vuex";
import Help from "@/components/Help.vue";
import QuestLog from "@/components/game/QuestLog.vue";
import ComLog from "@/components/game/sidebar/ComLog.vue";
import Focus from "@/components/game/sidebar/Focus.vue";
import Chars from "@/components/game/sidebar/Chars.vue";

const store = useStore();

const expanded = ref<"who" | "" | "chars">("");

const world = computed(() => store.state.game.world);
const allow_combat = computed(() => store.state.game.world.allow_combat);
const who_list = computed(() => store.state.game.who_list);
const room_chars_length = computed(() => store.state.game.room_chars.length);

const onClickWhoPlayer = (player) => {
  store.dispatch("game/cmd", `whois ${player.name}`);
};

const focus_help = `Set an item or character as the focus of another command.<br/>
    <br/>
    Enter 'help focus' for more information.`;

const onClickExpand = (section: "who" | "" | "chars") => {
  if (expanded.value == section) {
    expanded.value = "";
  } else {
    expanded.value = section;
  }
};

const onClickQuestLog = () => {
  store.commit('ui/modal/open_view', {
    component: QuestLog,
    options: {
      closeOnOutsideClick: true,
    },
  });
};

const onClickCommunicationLog = () => {
  store.commit('ui/modal/open_view', {
    component: ComLog,
    options: {
      closeOnOutsideClick: true,
    },
  });
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
#sidebar {
  background: $color-background-light;
  border-left: 2px solid $color-background-very-light;
  width: 250px;

  .sidebar-element {
    padding: 15px;
    &:not(:last-child) {
      border-bottom: 1px solid $color-background-very-light;
    }

    h3 {
      text-transform: uppercase;
      span {
        width: 10px;
        display: inline-block;
      }
    }

    &.who-list {
      .who-list-detail {
        .hover:hover {
          color: $color-text-hex-80;
        }
      }
    }
  }
}
</style>
