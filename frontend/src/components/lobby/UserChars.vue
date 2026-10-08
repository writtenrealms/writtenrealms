<template>
  <div class="world-chars-region" v-if="newCharacter || chars.length">
    <CreateChar v-if="newCharacter" :world="world" @charcreated="onCharCreated" />
    <!-- Without characters, the banner's create button is the only call to action. -->
    <div class="world-chars" v-else-if="chars.length">
      <div class="world-chars-header">
        <div class="world-chars-title">YOUR CHARACTERS</div>
        <input v-if="expanded" v-model="filter" type="search" class="chars-filter"
          placeholder="Filter by name or class" aria-label="Filter characters" />
      </div>

      <div class="world-chars-list">
        <!-- The whole row opens the character page; Play stays its own control. -->
        <div v-for="char in shownChars" :key="char.id" class="char-row" @click="openChar(char)">
          <div class="char-identity">
            <div class="char-name">
              <router-link class="char-name-text" :to="charRoute(char)" @click.stop>{{ char.name }}</router-link>
              <span v-if="char.is_builder" class="tag ml-2">Builder</span>
              <span v-if="world.is_multiplayer && !char.world_is_multi" class="tag ml-2">Single player</span>
            </div>
            <div class="char-info" v-if="world.allow_combat">{{ charInfo(char) }}</div>
          </div>
          <span class="char-played">{{ formatRelativeModifiedDate(char.last_connection_ts) }}</span>
          <button class="btn-small" @click.stop="playChar(char)">{{ needsTransfer(char) ? 'TRANSFER' : 'PLAY' }}</button>
          <ChevronRight class="char-open" aria-hidden="true" />
        </div>
        <p v-if="!shownChars.length" class="color-text-50 chars-empty">No characters match.</p>
      </div>
      <div class="chars-more" v-if="chars.length > VISIBLE_CHARS">
        <button class="btn-thin" @click="toggleExpanded">
          {{ expanded ? 'SHOW FEWER' : `SHOW ALL ${chars.length} CHARACTERS` }}
        </button>
        <span class="color-text-50">
          {{ expanded ? (filter ? `${shownChars.length} of ${chars.length} match` : `Showing all ${chars.length}`) : `Showing ${VISIBLE_CHARS} of ${chars.length}` }}
        </span>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref } from "vue";
import { useStore } from "vuex";
import { useRoute, useRouter } from "vue-router";
import { capfirst, formatRelativeModifiedDate } from "@/core/utils";
import { ChevronRight } from "@lucide/vue";
import { useCharEntry } from "@/composables/useCharEntry";
import CreateChar from "./CreateChar.vue";

const VISIBLE_CHARS = 5;

const store = useStore();
const route = useRoute();
const router = useRouter();
const { needsTransfer, playChar } = useCharEntry();

const chars = computed(() => store.state.lobby.chars || []);
const world = computed(() => store.state.lobby.world);
const newCharacter = computed(() => store.state.lobby.create_character);

const expanded = ref(false);
const filter = ref('');

onMounted(async () => {
  if (route.query.create && store.getters.isAuthenticated) {
    store.commit("lobby/create_character_set", true);
  }
});


const charInfo = (char) => {
  if (char.core_faction) {
    return `${capfirst(char.core_faction)} ${char.archetype}, level ${char.level}`;
  }
  return `${capfirst(char.gender)} ${char.archetype}, level ${char.level}`;
}

// Characters arrive most recently played first.
const shownChars = computed(() => {
  if (!expanded.value) return chars.value.slice(0, VISIBLE_CHARS);
  const query = filter.value.trim().toLowerCase();
  if (!query) return chars.value;
  return chars.value.filter(char =>
    `${char.name} ${charInfo(char)}`.toLowerCase().includes(query));
});

const toggleExpanded = () => {
  expanded.value = !expanded.value;
  filter.value = '';
}

const onCharCreated = () => {
  store.commit("lobby/create_character_set", false);
}

const charRoute = (char) => ({ name: 'character_details', params: { player_id: char.id } });
const openChar = (char) => router.push(charRoute(char));
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
@import "@/styles/layout.scss";

.world-chars-region {
  padding-top: 40px;
  margin-bottom: 100px;

  // Your characters
  .world-chars {
    .world-chars-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      min-height: 34px;
      margin-bottom: 10px;
    }

    .world-chars-title {
      @include font-title-regular;
      color: $color-secondary;
      letter-spacing: 1.5px;
      font-size: 15px;
      line-height: 18px;
    }

    .chars-filter {
      width: 220px;
      max-width: 100%;
      padding: 5px 10px;
      font-size: 13px;
      background: $color-form-background;
      border: 1px solid $color-form-border;
      border-radius: 2px;
      color: $color-text;
    }

    .char-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto auto;
      gap: 18px;
      align-items: center;
      padding: 13px 0;
      border-bottom: 1px solid $color-background-light-border;

      &:first-child {
        border-top: 1px solid $color-background-light-border;
      }

      cursor: pointer;

      &:hover {
        background: linear-gradient(90deg, $color-background-light, transparent 85%);

        .char-name-text { color: $color-secondary; }
        .char-open { color: $color-primary; }
      }

      @media ($mobile-site) {
        grid-template-columns: minmax(0, 1fr) auto auto;

        .char-played {
          display: none;
        }
      }
    }

    .char-name {
      @include font-text-regular;
      line-height: 22px;
      overflow-wrap: anywhere;
    }

    .char-info {
      @include font-text-light;
      font-size: 13px;
      line-height: 20px;
      color: $color-text-hex-60;
    }

    .char-played {
      @include font-title-light;
      font-size: 12px;
      letter-spacing: 0.5px;
      color: $color-text-hex-50;
      white-space: nowrap;
    }

    .char-name-text {
      color: $color-text;
      text-decoration: none;
    }

    .char-open {
      color: $color-text-hex-40;
    }

    .chars-empty {
      padding: 12px 0;
    }


    .chars-more {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      padding-top: 12px;
      font-size: 13px;

      .btn-thin {
        @include font-title-regular;
        font-size: 13px;
        letter-spacing: 1px;
        padding: 4px 0;
      }
    }
  }

  // New Character
  .new-character {
    .wrapper {
      background: $color-background-light;
      border: 1px solid $color-background-light-border;
      padding: 15px;
      width: 100%;

      .form-title {
        text-align: center;
        width: 100%;
        margin-bottom: 50px;
      }
    }
    .cancel-action {
      @include font-text-light;
      opacity: 0.5;
      width: 100%;
      text-align: center;
      &:hover {
        cursor: pointer;
      }
    }
  }
}
</style>
