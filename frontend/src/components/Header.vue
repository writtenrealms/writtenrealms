<template>
  <div id="header">
    <div class="header-view">
      <div class="logo">
        <router-link to="/home">
          <img alt="Written Realms logo" src="../assets/logo/logo-black.svg" class="logo-badge mr-1" />
          <img alt="Written Realms text" src="../assets/logo/written-realms.svg" class="logo-title" />
        </router-link>
      </div>

      <div class="menu" v-if="showMenu">
        <template v-if="isAuthenticated">
          <router-link to="/lobby" :class="{ 'selected': isLobbyActive }">{{ lobbyLabel }}</router-link>
          <router-link v-if="canCreateWorlds" :to="{ name: 'lobby_building' }">Build</router-link>
          <router-link v-if="store.state.auth.user?.is_staff" to="/staff" aria-label="Control Panel" :class="{ selected: router.currentRoute.value.path.startsWith('/staff') }">Control<span class="control-panel-suffix"> Panel</span></router-link>
          <router-link to="/account" :class="{ selected: router.currentRoute.value.path.startsWith('/account') }">Account</router-link>
          <a href="https://core.writtenrealms.com/docs/">Docs</a>
          <a href="#" @click.prevent="logout">Log Out</a>
        </template>

        <template v-else>
          <router-link to="/login">Log In</router-link>
          <router-link to="/signup">Sign Up</router-link>
        </template>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted } from 'vue';
import { useStore } from 'vuex';
import { useRouter } from 'vue-router';
import { loadPlatformConfig, platformConfig } from '@/core/platform';
import { useBuilding } from '@/composables/useBuilding';

const store = useStore();
const router = useRouter();

const { canCreateWorlds } = useBuilding();

const showMenu = true;
const isAuthenticated = computed(() => store.getters.isAuthenticated);
const lobbyLabel = computed(() => platformConfig.value?.main_world_id === null ? 'Worlds' : 'Lobby');
const isLobbyActive = computed(() => {
  const route = router.currentRoute.value;
  return route.name === 'lobby' || (
    route.name === 'lobby_world_details' &&
    String(route.params.world_id) === String(platformConfig.value?.main_world_id)
  );
});
onMounted(() => { loadPlatformConfig().catch(() => {}); });


const logout = () => {
  store.dispatch('auth/logout');
  router.push({ name: 'home' });
}

</script>

<style lang="scss" scoped>
@import "@/styles/layout.scss";
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

#header {
  background-color: $color-background-header;

  .header-view {
    max-width: $site-max-width;
    box-sizing: border-box;
    margin: 0 auto;
    height: 80px;
    padding: 0 15px;

    @media ($mobile-site) {
      min-height: 50px;
      height: auto;
      padding-top: 10px;
      padding-bottom: 10px;
    }

    display: flex;
    justify-content: space-between;
    align-items: center;

    .logo {
      flex-shrink: 0;
      a {
        text-decoration: none;
      }

      .logo-title {
        margin-bottom: 2px;
        margin-left: 3px;

        @media ($mobile-site) {
          display: none;
        }
      }
    }

    .menu {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      row-gap: 8px;
      min-width: 0;
      text-align: right;

      @media ($mobile-site) {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 8px 6px;

        .control-panel-suffix {
          display: none;
        }

      }

      a {
        @include font-title-light;
        font-size: 18px;
        color: $color-text;
        line-height: 18px;
        margin-left: 1em;
        opacity: 0.3;

        @media ($mobile-site) {
          margin-left: 0;
          font-size: 14px;
        }

        &:hover {
          color: $color-primary;
          opacity: 1;
          text-decoration: underline;
        }

        &.selected {
          color: $color-text;
          opacity: 1;

          &:hover {
            cursor: default;
            text-decoration: none;
          }
        }
      }
    }
  }
}
</style>
