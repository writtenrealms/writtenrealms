<template>
  <div id="world_lobby" v-if="world && loaded" :key="String(world.id)">
    <div class="world-hero">
      <div class="world-hero-art" :style="backgroundImage"></div>
      <div class="world-hero-content">
        <h1 class="world-title">{{ world.name }}</h1>
        <div class="world-tools">
          <button class="icon-button" title="Copy link to this world" aria-label="Copy link to this world"
            @click="copyShareLink">
            <Share aria-hidden="true" />
          </button>
          <router-link v-if="world.can_edit" class="icon-button" title="Edit this world" aria-label="Edit this world"
            :to="{ name: 'builder_world_index', params: { world_id: route.params.world_id } }">
            <Pencil aria-hidden="true" />
          </router-link>
        </div>
        <div class='instance-of' v-if="world.instance_of.name">
          <span class='color-text-50'>INSTANCE OF&nbsp;</span>
          <router-link :to="{ name: 'lobby_world_details', params: { world_id: world.instance_of.id } }">
            {{ world.instance_of.name.toUpperCase() }}
          </router-link>
        </div>
        <div class="world-actions" v-if="!create_character">
          <template v-if="!isAuthenticated">
            <router-link class="btn-medium" role="button" :to="signupRoute">SIGN UP TO PLAY</router-link>
            <router-link class="btn-medium button-gray" role="button"
              :to="{ name: 'login', query: { redirect: route.path } }">LOG IN</router-link>
          </template>
          <template v-else-if="resumeChar">
            <button class="btn-medium" @click="playChar(resumeChar)">
              {{ needsTransfer(resumeChar) ? 'TRANSFER' : 'CONTINUE AS' }} {{ resumeChar.name.toUpperCase() }}
            </button>
            <button class="btn-medium button-gray" @click="onClickCreateChar">NEW CHARACTER</button>
          </template>
          <button v-else class="btn-medium" @click="onClickCreateChar">CREATE A CHARACTER</button>
        </div>
      </div>
    </div>

    <div class="world-body">
      <div class="world-main" :class="{ creating: create_character }">
        <div class="world-description">
          <div class="desc-line" v-for="(line, index) of descLines" :key="index">{{ line }}</div>
        </div>

        <UserChars />
      </div>

      <div class="world-leaderboard" v-if="!create_character && (leaderboards.length || leaderboardError)">
        <LeaderboardPanels :panels="leaderboards" :error="leaderboardError" :loading="leaderboardsLoading"
          :own-player-ids="ownPlayerIds"
          @retry="store.dispatch('lobby/fetch_leaderboards', route.params.world_id)" />
      </div>
    </div>
  </div>
  <div v-else-if="loadError" class="world-lobby-error" role="alert">
    <p>{{ loadError }}</p>
    <router-link v-if="!store.getters.isAuthenticated"
      :to="{ name: 'login', query: { redirect: route.fullPath } }">Log in</router-link>
    <button class="btn-small" @click="loadWorld">RETRY</button>
  </div>
  <div v-else class="loading-container">
    <div class="spinner"></div>
  </div>
</template>

<script lang="ts" setup>
import { computed, nextTick, ref, watch }  from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import UserChars from "@/components/lobby/UserChars.vue";
import LeaderboardPanels from "@/components/lobby/LeaderboardPanels.vue";
import { Pencil, Share } from "@lucide/vue";
import { useCharEntry } from "@/composables/useCharEntry";
import defaultBackground from "@/assets/ui/world-home-bg.jpg";

const store = useStore();
const route = useRoute();
const router = useRouter();

const loaded = ref(false);
const loadError = ref('');

const world = computed(() => store.state.lobby.world);
const leaderboards = computed(() => store.state.lobby.leaderboards);
const leaderboardError = computed(() => store.state.lobby.leaderboardError);
const leaderboardsLoading = computed(() => store.state.lobby.leaderboardsLoading);
const create_character = computed(() => store.state.lobby.create_character);
const isAuthenticated = computed(() => store.getters.isAuthenticated);
const chars = computed(() => store.state.lobby.chars || []);
const { needsTransfer, playChar } = useCharEntry();

// Characters arrive most recently played first.
const resumeChar = computed(() => chars.value[0]);
const ownPlayerIds = computed(() => chars.value.map(char => char.id));

const backgroundImage = computed(() => ({
  backgroundImage: `url(${world.value.large_background || defaultBackground})`,
}));

// Visitors sign up on their own page and come back with character creation open.
const signupRoute = computed(() => ({
  name: 'signup',
  query: { redirect: router.resolve({ path: route.path, query: { create: '1' } }).fullPath },
}));

const onClickCreateChar = () => {
  if (!isAuthenticated.value) {
    router.push(signupRoute.value);
    return;
  }
  store.commit("lobby/create_character_set", true);
};

// The creation form (or sign-up for visitors) opens above the description;
// bring it into view since the button that opened it is in the banner.
watch(create_character, async (creating) => {
  if (!creating) return;
  await nextTick();
  document.getElementById('lobby-new-character')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

const descLines = computed(() => world.value.description.split("\n"));

const copyShareLink = async () => {
  try {
    const slug = world.value.name.toLowerCase().split(/\W+/).join("-");
    const url = `${window.location.origin}/worlds/${route.params.world_id}/${slug}`;
    await navigator.clipboard.writeText(url);
    store.commit("ui/notification_set", "World URL copied to clipboard!");
  } catch (err) {
    console.error('Failed to copy text: ', err);
    store.commit("ui/notification_set", "Failed to copy URL to clipboard.");
  }
};

async function loadWorld() {
  const worldId = route.params.world_id;
  loaded.value = false;
  loadError.value = '';
  try {
    if (store.state.auth.user.is_temporary) {
      await store.dispatch('auth/logout');
    }
    await store.dispatch('lobby/initial_fetch', worldId);
    if (route.params.world_id !== worldId) return;
    if (route.query.create && !isAuthenticated.value) {
      router.replace(signupRoute.value);
      return;
    }
    loaded.value = true;
  } catch (error: any) {
    if (route.params.world_id !== worldId) return;
    const status = error.response?.status;
    loadError.value = status === 401 || status === 403
      ? store.getters.isAuthenticated
        ? 'This account does not have access to this world.'
        : 'This world is private. Log in with an account that has access.'
      : status === 404
        ? 'This world is unavailable. It may not have been set up yet.'
        : 'This world could not be loaded. Please try again.';
  }
}

watch(() => route.params.world_id, (worldId) => {
  if (worldId) loadWorld();
}, { immediate: true });
</script>

<style lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
@import "@/styles/layout.scss";

.world-lobby-error {
  max-width: 600px;
  margin: 60px auto;
  padding: 0 20px;

  a, button { margin: 20px 20px 0 0; }
}

#world_lobby {
  width: 100%;

  // Banner art spans the full width at its own proportions (banners are
  // 2300x598) and dissolves into the page; the title sits over its lower part.
  .world-hero {
    position: relative;
    isolation: isolate;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    // Never shorter than the art, so the page below can't slide under it.
    min-height: max(180px, calc(100vw * 598 / 2300));

    @media ($mobile-site) {
      min-height: 200px;
    }
  }

  .world-hero-art {
    position: absolute;
    z-index: -1;
    top: 0;
    left: 0;
    width: 100%;
    aspect-ratio: 2300 / 598;
    min-height: 180px;
    background-color: #332d25;
    background-position: center top;
    background-size: cover;
    background-repeat: no-repeat;

    @media ($mobile-site) {
      min-height: 200px;
      background-position: 62% top;
    }

    &::after {
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(90deg, rgba(25, 26, 28, 0.7) 0%, rgba(25, 26, 28, 0.3) 35%, $color-transparent-rgba 60%),
        linear-gradient(180deg, $color-transparent-rgba 0%, $color-transparent-rgba 45%, rgba(25, 26, 28, 0.75) 80%, $color-background-rgba 100%);
    }
  }

  .world-hero-content,
  .world-body {
    width: 100%;
    max-width: 1150px;
    margin: 0 auto;
    padding-left: 80px;
    padding-right: 30px;

    @media ($mobile-site) {
      padding-left: 15px;
      padding-right: 15px;
    }
  }

  .world-hero-content {
    padding-top: 130px;
    padding-bottom: 34px;

    @media ($mobile-site) {
      padding-top: 120px;
    }

    .world-title {
      @include font-title-regular;
      font-size: 56px;
      line-height: 1;
      letter-spacing: 4px;
      text-transform: uppercase;
      text-shadow: 0 2px 18px rgba(0, 0, 0, 0.5);
      margin: 0;

      @media ($mobile-site) {
        font-size: 38px;
        letter-spacing: 2.5px;
      }
    }

    .world-tools {
      display: flex;
      gap: 6px;
      margin: 10px 0 0 -6px;
    }

    .icon-button {
      color: $color-text-70;

      &:hover {
        color: $color-primary;
        background: rgba(18, 19, 21, 0.45);
      }
    }

    .instance-of {
      @include font-title-regular;
      font-size: 13px;
      letter-spacing: 0.83px;
      word-spacing: 0.5em;
      line-height: 16px;
      margin-top: 10px;
    }

    .world-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 26px;

      a {
        text-decoration: none;
      }
    }

    // Keeps the secondary button legible over the banner art.
    .button-gray {
      background-color: rgba(18, 19, 21, 0.35);

      &:hover {
        background-color: rgba(18, 19, 21, 0.6);
      }
    }
  }

  .world-body {
    display: flex;
    gap: 64px;
    padding-top: 10px;
    padding-bottom: 50px;

    @media ($mobile-site) {
      flex-direction: column;
      gap: 40px;
    }

    .world-main {
      flex: 1.75;
      min-width: 0;
      display: flex;
      flex-direction: column;

      &.creating .world-description {
        order: 1;
        margin-top: 40px;
      }
    }

    .world-leaderboard {
      flex: 1;
      min-width: 0;
    }
  }

  .world-description {
    @include font-text-light;
    font-size: 16px;
    line-height: 28px;
    max-width: 64ch;

    div.desc-line:not(:last-child) {
      margin-bottom: 0.8em;
    }
  }
}
</style>
