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
          <a href="#" @click.prevent="editAccount" v-if="!isTemporary">Account</a>
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
import { FormElement, EntityForm } from '@/core/forms';
import ConfirmEmail from '@/components/account/ConfirmEmail.vue';
import StaffAccount from '@/components/account/StaffAccountSlot.vue';
import { loadPlatformConfig, platformConfig } from '@/core/platform';
import { useBuilding } from '@/composables/useBuilding';

const store = useStore();
const router = useRouter();

const { canCreateWorlds } = useBuilding();

const showMenu = true;
const isAuthenticated = computed(() => store.getters.isAuthenticated);
const isTemporary = false;
const lobbyLabel = computed(() => platformConfig.value?.main_world_id === null ? 'Worlds' : 'Lobby');
const isLobbyActive = computed(() => {
  const route = router.currentRoute.value;
  return route.name === 'lobby' || (
    route.name === 'lobby_world_details' &&
    String(route.params.world_id) === String(platformConfig.value?.main_world_id)
  );
});
onMounted(() => { loadPlatformConfig().catch(() => {}); });


const editAccount = () => {

  const SEND_NEWSLETTER: FormElement = {
    attr: "send_newsletter",
    label:
      "Receive infrequent (less than monthly) updates about new game features.",
    widget: "checkbox"
  };

  const ACCESSIBILITY: FormElement = {
    attr: "accessibility_mode",
    label: "Use Accessibility Layout",
    widget: "checkbox"
  };

  const edit_account_schema = [
    {
      attr: "name",
      label: "Username"
    },
    SEND_NEWSLETTER,
    ACCESSIBILITY,
  ];

  // Optional email confirm / staff slot
  let slot: any = null;
  if (store.state.auth.user.is_staff) {
    slot = StaffAccount;
  } else if (store.state.auth.user.is_invalid) {
    // Could also write a message here
    slot = ConfirmEmail;
  } else if (!store.state.auth.user.is_confirmed) {
    slot = ConfirmEmail;
  }

  const formData: EntityForm = {
    title: store.state.auth.user.email,
    schema: edit_account_schema,
    data: store.state.auth.user,
    action: "auth/account_save",
    submitLabel: "Save Changes",
    slot: slot,
  };

  store.commit('ui/modal/open_form', formData);
}

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
      text-align: right;

      @media ($mobile-site) {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 8px 12px;

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
