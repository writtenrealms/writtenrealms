<template>
  <div class="world-chars-region">
    <CreateChar v-if="newCharacter" :world="world" @charcreated="onCharCreated" />
    <div class="world-chars" v-else>
      <div class="world-chars-header">
        <div class="world-chars-title">YOUR CHARACTERS</div>
        <input v-if="expanded" v-model="filter" type="search" class="chars-filter"
          placeholder="Filter by name or class" aria-label="Filter characters" />
      </div>

      <div class="world-chars-list" v-if="chars.length">
        <div v-for="char in shownChars" :key="char.id" class="char-row">
          <div class="char-identity">
            <div class="char-name">
              <span class="char-name-text">{{ char.name }}</span>
              <span v-if="char.is_builder" class="color-text-50 ml-2">[ Builder ]</span>
              <span v-if="world.is_multiplayer && !char.world_is_multi" class='color-text-50 ml-2'>[ SPW ]</span>
            </div>
            <div class="char-info" v-if="world.allow_combat">{{ charInfo(char) }}</div>
          </div>
          <span class="char-played">{{ formatRelativeModifiedDate(char.last_connection_ts) }}</span>
          <button class="btn-small" @click="playChar(char)">{{ needsTransfer(char) ? 'TRANSFER' : 'PLAY' }}</button>
          <div class="char-more">
            <button class="icon-button" :aria-label="`More actions for ${char.name}`"
              @click="onClickMoreActions(char.id)">
              <Ellipsis aria-hidden="true" />
            </button>
            <UserCharActions
              :player="char"
              v-if="more_actions[char.id]"
              @close="onCloseCharActions"/>
          </div>
        </div>
        <p v-if="!shownChars.length" class="color-text-50 chars-empty">No characters match.</p>
      </div>
      <div v-else class="chars-none">
        <p class="color-text-60">You don't have a character in this world yet.</p>
        <button class="btn-add" @click="onClickCreateChar()">CREATE NEW CHARACTER</button>
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
import { useRoute } from "vue-router";
import { capfirst, formatRelativeModifiedDate } from "@/core/utils";
import { Ellipsis } from "@lucide/vue";
import { useCharEntry } from "@/composables/useCharEntry";
import CreateChar from "./CreateChar.vue";
import UserCharActions from "./UserCharActions.vue";

const VISIBLE_CHARS = 5;

const store = useStore();
const route = useRoute();
const { needsTransfer, playChar } = useCharEntry();

const chars = computed(() => store.state.lobby.chars || []);
const world = computed(() => store.state.lobby.world);
const newCharacter = computed(() => store.state.lobby.create_character);

const expanded = ref(false);
const filter = ref('');

let more_actions = ref({});

onMounted(async () => {
  if (route.query.create) {
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

const  onClickCreateChar = () => {
  store.commit("lobby/create_character_set", true);
}

const onClickMoreActions = (char_id) => {
    if (more_actions.value[char_id]) {
      more_actions.value = {};
    } else {
      more_actions.value = {};
      more_actions.value[char_id] = true;
    }
  }

const onCloseCharActions = () => {
    more_actions.value = {};
}
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

    .char-more {
      position: relative;

      .expanded-actions {
        position: absolute;
        right: 0;
        top: 26px;
        z-index: 10;
        border: 1px solid #333;
        border-top: 0px;
        background: #222;
        box-shadow: 0 5px 15px rgba(0, 0, 0, 0.25);
      }
    }

    .chars-empty {
      padding: 12px 0;
    }

    .chars-none {
      p {
        margin-bottom: 16px;
      }
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
