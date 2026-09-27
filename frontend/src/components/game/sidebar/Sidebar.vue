<template>
  <div id="sidebar">
    <div class="sidebar-element logs" role="group" aria-label="Logs">
      <button type="button" class="log-link" aria-label="Quest Log" @click="onClickQuestLog">QUESTS</button>
      <template v-if="world.is_multiplayer">
        <span class="log-divider" aria-hidden="true">|</span>
        <button type="button" class="log-link" aria-label="Communication Log" @click="onClickCommunicationLog">COMS</button>
      </template>
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
          :class="{ 'color-secondary': player.name_recognition, 'color-primary': player.is_builder }"
          @click="onClickWhoPlayer(player)"
        >
          {{ player.name }} {{ player.title }}
          <span v-if="player.is_idle" class='ml-1 color-text-50'>(Idle)</span>

        </div>
      </div>
    </div>

    <div v-if="allow_combat" class="sidebar-element combat">
      <CombatRoster />
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
import CombatRoster from "@/components/game/sidebar/CombatRoster.vue";

const store = useStore();

const expanded = ref<"who" | "">("");

const world = computed(() => store.state.game.world);
const allow_combat = computed(() => store.state.game.world.allow_combat);
const who_list = computed(() => store.state.game.who_list);

const onClickWhoPlayer = (player) => {
  store.dispatch("game/cmd", `whois ${player.name}`);
};

const focus_help = `Set an item or character as the focus of another command.<br/>
    <br/>
    Enter 'help focus' for more information.`;

const onClickExpand = (section: "who" | "") => {
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
  overflow-y: auto;

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

    &.logs {
      display: flex;
      position: relative;
      padding: 0;
      white-space: nowrap;

      .log-link {
        @include font-title-regular;
        flex: 1 1 0;
        min-width: 0;
        min-height: 54px;
        display: flex;
        align-items: center;
        justify-content: center;
        appearance: none;
        background: rgba(255, 255, 255, 0.025);
        border: 0;
        padding: 12px 8px;
        color: $color-text-hex-70;
        font-size: 1rem;
        line-height: 1.5;
        text-align: center;
        cursor: pointer;

        &:hover { color: $color-text; background: rgba(255, 255, 255, 0.06); }
        &:active { background: rgba(255, 255, 255, 0.09); }
        &:focus-visible { outline: 1px solid $color-primary; outline-offset: -3px; }
      }

      .log-divider {
        position: absolute;
        left: 50%;
        top: 50%;
        transform: translate(-50%, -50%);
        color: $color-text-hex-30;
        pointer-events: none;
      }
    }
  }
}
</style>
