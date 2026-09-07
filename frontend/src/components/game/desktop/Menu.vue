<template>
    <div class="menu-region" ref="outsideClickRef">
      <div class="callout-actions point-up">
        <template v-if="user.is_temporary">
          <div class="action create-account" @click="saveCharacter">Save Character</div>
          <div class="action exit-demo" @click="onClickExit">Exit Introduction</div>
        </template>
        <template v-else>
          <div class="action" @click="onClickSettings">
            <a href="#" class='settings' @click.prevent>Settings</a>
          </div>
          <div class="action" v-if="world.id === 2">
            <a href="#" @click.prevent="onClickMap">World Map</a>
          </div>
          <div class="action" @click="onClickDocumentation">
            <a href="https://docs.writtenrealms.com" target="_blank">Documentation</a>
          </div>
          <div class="action" @click="onClickChatOnDiscord">
            <a href="https://discord.gg/a3u82tR" target="_blank">Chat on Discord</a>
          </div>
          <div class="action" @click="onClickPatreon">
            <a href="https://www.patreon.com/writtenrealms">Support Us</a>
          </div>
          <div class="action" @click="onClickLeave" v-if="store.state.game.world.instance_of_id">
            <a href="#" class="exit-game" @click.prevent>Leave Instance</a>
          </div>
          <div class="action" @click="onClickExit" v-else>
            <a href="#" class="exit-game" @click.prevent>Exit World</a>
          </div>
        </template>
      </div>
    </div>
</template>


<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import SaveUser from "@/components/account/SaveUser.vue"
import WorldMap from "@/components/game/WorldMap.vue";
import Settings from "@/components/game/Settings.vue";
import { onOutsideClick } from "@/composables/onOutsideClick";

const store = useStore();

const emit = defineEmits(['close']);

const outsideClickRef = onOutsideClick(() => {
  emit('close');
});

const world: any = computed(() => store.state.game.world);
const user: any = computed(() => store.state.auth.user);


const onClickDocumentation = () => {
  emit('close');
  window.open("https://docs.writtenrealms.com", "_blank");
}

const onClickChatOnDiscord = () => {
  emit('close');
  window.open('https://discord.gg/a3u82tR', "_blank");
}

const onClickPatreon = () => {
  emit('close');
  window.open("https://www.patreon.com/writtenrealms", "_blank");
}

const onClickExit = () => {
  emit('close');
  store.dispatch("game/cmd", "quit");
}

const onClickLeave = () => {
  emit('close');
  store.dispatch("game/cmd", "leave");
}

const onClickSettings = () => {
  emit('close');
  store.commit("ui/modal/open_view", { component: Settings });
};

const onClickMap = () => {
  emit('close');
  store.commit('ui/modal/open_view', {component: WorldMap});
}

const saveCharacter = () => {
  emit('close');
  store.commit('ui/modal/open_view', {
    component: SaveUser,
    options: {
      overlayClasses: ["save_user"],
      closeOnOutsideClick: true
    },
  });
}
</script>


<style lang='scss' scoped>
.menu-region {
  position: relative;

  .callout-actions {
    right: -31px;
    top: 3px;
  }
}
</style>