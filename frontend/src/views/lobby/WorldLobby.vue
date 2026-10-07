<template>
  <div id="world_lobby" v-if="world && loaded" :key="String(world.id)">
    <div class="world-details">
      <div class="world-home-bg" :style="backgroundImage">
        <div class="world-home-bg-overlay"></div>
      </div>

      <div class="world-home flex">
        <div class="world-home-left-side">
          <div class="world-info">
            <div class="world-title">{{ world.name }}</div>
            <div class="world-author">
              By {{ world.built_by }}
            </div>
            <div class='world-misc mt-1'>
              <span class='color-text-50 world-type'>
                {{ world_descriptors }}
              </span>
              <span class="divider"></span>
              <a href="javascript:void(0)" @click.prevent="copyShareLink">SHARE</a>
              <template v-if="world.can_edit">
                <span class="divider"></span>
                <router-link
                  :to="{ name: 'builder_world_index', params: { world_id: route.params.world_id } }">EDIT</router-link>
              </template>
            </div>
            <div class='instance-of' v-if="world.instance_of.name">
              <span class='color-text-50'>INSTANCE OF&nbsp;</span>
              <router-link :to="{ name: 'lobby_world_details', params: { world_id: world.instance_of.id } }">
                {{ world.instance_of.name.toUpperCase() }}
              </router-link>
            </div>

            <div class="world-description mt-4">
              <div class="desc-line" v-for="(line, index) of descLines" :key="index">{{ line }}</div>
            </div>
          </div>

          <UserChars />
        </div>

        <div class="world-leaderboard" v-if="!create_character && (leaderboards.length || leaderboardError)">
          <LeaderboardPanels :panels="leaderboards" :error="leaderboardError" :loading="leaderboardsLoading"
            @retry="store.dispatch('lobby/fetch_leaderboards', route.params.world_id)" />
        </div>
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
import { computed, ref, watch }  from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import UserChars from "@/components/lobby/UserChars.vue";
import LeaderboardPanels from "@/components/lobby/LeaderboardPanels.vue";

const store = useStore();
const route = useRoute();

const loaded = ref(false);
const loadError = ref('');

const world = computed(() => store.state.lobby.world);
const leaderboards = computed(() => store.state.lobby.leaderboards);
const leaderboardError = computed(() => store.state.lobby.leaderboardError);
const leaderboardsLoading = computed(() => store.state.lobby.leaderboardsLoading);
const create_character = computed(() => store.state.lobby.create_character);

const backgroundImage = computed(() => {
  if (world && world.value.large_background) {
    return { backgroundImage: `url(${world.value.large_background})` };
  }
  return {};
});

const descLines = computed(() => world.value.description.split("\n"));

async function loadWorld() {
  const worldId = route.params.world_id;
  loaded.value = false;
  loadError.value = '';
  try {
    if (store.state.auth.user.is_temporary) {
      await store.dispatch('auth/logout');
    }
    await store.dispatch('lobby/initial_fetch', worldId);
    if (route.params.world_id === worldId) loaded.value = true;
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

const world_descriptors = computed(() => {
  let descriptor = "";

  if (world.value.is_private) {
    descriptor += "PRIVATE ";
  }

  if (world.value.is_narrative) {
    descriptor += "NARRATIVE ";
  }

  const world_type = world.value.is_multiplayer
    ? "MULTIPLAYER"
    : "SINGLEPLAYER";
  return `${descriptor} ${world_type} WORLD`;
});

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
  //padding: 50px 20px;
  max-width: 1150px;
  margin: 0 auto;

  .world-details {
    flex-grow: 1;

    max-width: 1150px;
    margin: 0 auto;
    position: relative;

    .world-home-bg {
      position: absolute;
      background: #332d25;
      // background: url("/ui/lobby/world-home-bg.jpg");
      background: url("@/assets/ui/world-home-bg.jpg");
      background-size: 1150px 299px;
      width: 100%;
      height: 299px;
    }

    .world-home-bg-overlay {
      background: linear-gradient(to bottom,
          $color-transparent-rgba,
          $color-background-rgba 100%);
      position: absolute;
      width: 100%;
      height: 299px;
    }

    .world-home {
      position: absolute;
      z-index: 10000;
      width: 100%;
      margin-top: 150px;
      margin-bottom: 50px;

      flex-direction: row;

      @media ($mobile-site) {
        flex-direction: column;
        margin-top: 50px;
      }

      .world-home-left-side {
        flex: 2;

        .world-title {
          text-transform: uppercase;
        }
      }

      .world-leaderboard {
        flex: 1;

        @media ($mobile-site) {
          margin-top: 50px;
        }
      }
    }

    .world-home-left-side {
      padding: 0 30px;

      @media ($desktop-site) {
        padding-left: 80px;
      }

      @media ($mobile-site) {
        padding: 0 15px;
      }

      //@include media-breakpoint-up(lg) {
      //  padding-left: 80px;
      //}

      // Details of the world
      .world-info {
        .world-title {
          @include font-title-regular;
          font-size: 30px;
          letter-spacing: 1.3px;
          line-height: 35px;
        }

        .world-author,
        .world-misc {
          @include font-text-light;
          font-size: 15px;
          letter-spacing: -0.5px;
          line-height: 20px;
          // margin-bottom: 1em;

          a,
          .world-type {
            @include font-title-regular;
            font-size: 13px;
            letter-spacing: 0.83px;
            word-spacing: 0.5em;
            line-height: 16px;
          }

          a {
            color: $color-primary;

            &:hover {
              text-decoration: underline;
            }
          }

          span.divider {
            border-left: 1px solid white;
            opacity: 0.2;
            margin-left: 12px;
            padding-left: 12px;
          }
        }
        .instance-of {
          border-top: 1px solid $color-background-border;
          margin-top: 5px;
          padding-top: 5px;
          width: 350px;

          @include font-title-regular;
          font-size: 13px;
          letter-spacing: 0.83px;
          word-spacing: 0.5em;
          line-height: 16px;
        }

        .world-description {
          @include font-text-light;
          font-size: 15px;
          line-height: 26px;

          div.desc-line:not(:last-child) {
            margin-bottom: 0.8em;
          }
        }
      }
    }

    // Leaderboard
    .world-leaderboard {
      padding: 0 30px;
      // @include media-breakpoint-up(lg) {
      //   margin-top: 130px;
      //   padding: 0 15px 0 50px;
      // }
      // @include media-breakpoint-down(md) {
      //   padding-top: 50px;
      // }
      padding-top: 146px;

      @media ($mobile-site) {
        padding: 0 15px 0 15px;
      }

      .leaderboard-title {
        @include font-title-regular;
        color: $color-secondary;
        font-size: 15px;
        letter-spacing: 1.5px;
        line-height: 18px;
        margin-bottom: 20px;
      }

      ul {
        list-style-type: none;
        padding: 0;

        li.world-leader {
          line-height: 23px;

          .index {
            color: $color-secondary;
            margin-right: 0.5em;
          }
        }
      }
    }
  }
}
</style>
