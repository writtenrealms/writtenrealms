<template>
  <div id="lobby-new-character">
    <div class="new-char-signup" v-if="showSignup">
      <SignUp :redirect="route.fullPath" />
    </div>

    <div class="wrapper" v-else>
      <form class @submit.prevent="createCharacter">
        <h1 class="form-title">CREATE NEW CHARACTER</h1>
        <div
          class="creation-fields"
          :class="{ 'has-factions': showFactions, 'has-archetype': showArchetype }"
        >
          <!-- Name -->
          <div class="form-group field-name">
            <label for="field-name">Name</label>
            <input
              id="field-name"
              type="name"
              class="form-control"
              placeholder="Name"
              v-model="charname"
              :required="true"
            />
          </div>

          <!-- Gender -->
          <div class="form-group field-gender">
            <label for="field-gender">Gender</label>
            <select
              id="field-gender"
              v-model="gender"
              :disabled="!world.can_select_gender"
              :readonly="!world.can_select_gender"
            >
              <option value="male">Male</option>
              <option value="female">Female</option>
              <option value="non_binary">Non-Binary</option>
            </select>
          </div>

          <!-- Factions -->
          <div class="form-group field-faction" v-if="showFactions">
            <label for="field-faction">Faction</label>
            <select id="field-faction" v-model="faction">
              <option
                v-for="faction in worldFactions"
                :key="faction.code"
                :value="faction.code"
                >{{ faction.name }}</option
              >
            </select>
          </div>

          <!-- Archetype -->
          <div class="form-group field-archetype" v-if="showArchetype">
            <div class="flex">
              <label for="field-archetype">Class</label>
              <Help v-if="!showFactions" :help="archetypeHelp" />
            </div>
            <select
              id="field-archetype"
              v-model="archetype"
              :disabled="!classChoiceEnabled"
              :readonly="!classChoiceEnabled"
            >
              <option
                v-for="option in classOptions"
                :key="option.key"
                :value="option.key"
              >{{ option.label }}</option>
            </select>
          </div>

          <!-- Descriptions -->
          <template v-if="showArchetype || showFactions">
            <div class="form-group field-description field-faction-description" v-if="showFactions">
              <div class="inner-description">
                <div
                  v-for="(line, index) in factionDescriptionLines"
                  :key="index"
                  class="faction-description-line"
                >
                  {{ line }}
                </div>
              </div>
            </div>
            <div class="form-group field-description field-archetype-description" v-if="showArchetype">
              <div class="inner-description">
                {{ archetypeDescription(archetype) }}
              </div>
            </div>
          </template>
        </div>

        <button class="btn-large">CREATE CHARACTER</button>
      </form>
    </div>
    <div class="cancel-action" @click="onCancelCreate()">cancel</div>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref, watchEffect } from 'vue';
import { useStore } from 'vuex';
import { useRouter, useRoute } from 'vue-router';
import axios from "axios";
import _ from "lodash";
import SignUp from "@/views/auth/SignUp.vue";
import Help from "@/components/Help.vue";
import { INTRO_WORLD_ID } from "@/config";
import { capfirst } from "@/core/utils";

const emit = defineEmits(['charcreated']);

const store = useStore();
const router = useRouter();
const route = useRoute();
const world = computed(() => store.state.lobby.world);
const charname = ref("");
const defaultGender = () => {
  if (world.value?.can_select_gender === false && world.value?.default_gender) {
    return world.value.default_gender;
  }
  return "male";
};
const gender = ref(defaultGender());
const archetype = ref("");
const faction = ref(null);

const fallbackClassOptions = [
  { key: "warrior", label: "Warrior" },
  { key: "mage", label: "Mage" },
  { key: "cleric", label: "Cleric" },
  { key: "assassin", label: "Assassin" },
];

const classOptions = computed(() => {
  const labels = world.value?.labels?.classes || {};
  const options = Object.entries(labels).map(([key, label]) => ({
    key,
    label: String(label || capfirst(key)),
  }));
  if (!options.length && world.value && !world.value.is_classless) {
    return fallbackClassOptions;
  }
  return options;
});

const classChoiceEnabled = computed(() => world.value?.class_selection?.enabled !== false);

const defaultArchetype = computed(() => {
  const configured = world.value?.class_selection?.default;
  if (configured && classOptions.value.some(option => option.key === configured)) {
    return configured;
  }
  return classOptions.value[0]?.key || "warrior";
});

const worldFactions = computed(() => {
  const selectable = world.value.core_factions.filter(f => f.is_selectable);
  const sorted = _.sortBy(selectable, f => !f.is_default);
  if (sorted.length) {
    faction.value = sorted[0].code;
  }
  return sorted;
});

const showFactions = computed(() => world.value.core_factions.length > 0 && world.value.can_select_faction);
const showSignup = computed(() => !store.state.auth.token);
const showArchetype = computed(() => !(!world.value.allow_combat || world.value.is_classless || router.currentRoute.value.params.world_id == INTRO_WORLD_ID) && classChoiceEnabled.value && classOptions.value.length > 0);
const factionData = computed(() => world.value.core_factions.find(f => f.code === faction.value) || false);

const factionDescriptionLines = computed(() => factionData.value.description ? factionData.value.description.split("\n") : []);

function archetypeDescription(archetype) {
  switch(archetype) {
    case "warrior": return "Warriors are hardened brawlers, smashing their enemies head-on.";
    case "warlord": return "Warlords are hardened brawlers, smashing their enemies head-on.";
    case "mage": return "Mages are glass cannons, masters of elemental damage.";
    case "tidecaller": return "Tidecallers are glass cannons, masters of elemental damage.";
    case "assassin": return "Assassins are masters of stealth and violence.";
    case "cleric": return "Clerics are masters of healing and survival.";
    case "mystic": return "Mystics are masters of healing and survival.";
    case "hoplite": return "Hoplites are disciplined front-line fighters.";
    default: return "";
  }
}

const archetypeHelp = computed(() => {
  return classOptions.value
    .map(option => {
      const description = archetypeDescription(option.key);
      return description ? `${option.label} - ${description}` : option.label;
    })
    .join("<br/><br/>");
});

watchEffect(() => {
  if (!showArchetype.value) {
    return;
  }
  const selectedIsValid = classOptions.value.some(option => option.key === archetype.value);
  if (!selectedIsValid || !classChoiceEnabled.value) {
    archetype.value = defaultArchetype.value;
  }
});

async function createCharacter() {
  const payload = {
    name: charname.value,
    gender: gender.value,
    archetype: world.value.is_classless ? '' : archetype.value || defaultArchetype.value,
    faction: faction.value
  };

  const response = await axios.post(`lobby/worlds/${router.currentRoute.value.params.world_id}/chars/`, payload);
  if (response.status === 201) {
    store.commit('lobby/char_create', response.data);
    charname.value = "";
    gender.value = defaultGender();
    emit('charcreated', response.data);
  }
}

function onCancelCreate() {
  store.commit("lobby/create_character_set", false);
}
</script>

<style lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";
@import "@/styles/fonts.scss";

#lobby-new-character {
  max-width: 800px;
  margin: 0 auto;
  margin-bottom: 50px;
  margin-top: 40px;
  flex-basis: 33%;

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

    .creation-fields {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      column-gap: 2rem;
      align-items: start;

      .form-group {
        min-width: 0;
      }

      .field-name {
        grid-column: 1;
        grid-row: 1;
      }

      .field-gender {
        grid-column: 2;
        grid-row: 1;
      }

      .field-faction {
        grid-column: 1;
        grid-row: 2;
      }

      .field-archetype {
        grid-column: 2;
        grid-row: 2;
      }

      .field-faction-description {
        grid-column: 1;
        grid-row: 3;
      }

      &.has-factions .field-archetype-description {
        grid-column: 2;
        grid-row: 3;
      }

      &.has-archetype:not(.has-factions) .field-gender {
        grid-column: 1;
        grid-row: 2;
      }

      &.has-archetype:not(.has-factions) .field-archetype {
        grid-column: 2;
        grid-row: 1;
      }

      &.has-archetype:not(.has-factions) .field-archetype-description {
        grid-column: 2;
        grid-row: 2;
      }

      @media ($mobile-site) {
        grid-template-columns: minmax(0, 1fr);
        column-gap: 0;

        .form-group {
          grid-column: 1 !important;
          grid-row: auto !important;
        }
      }
    }

    // Used for both archetype & faction descriptions
    .field-description {
      .field-name {
        @include font-title-light;
        color: $color-secondary;
        font-size: 15px;
        letter-spacing: 0;
        padding-bottom: 0.5rem;
      }
      .inner-description {
        .faction-description-line {
          min-height: 0.6rem;
        }
      }
    }

    .creation-fields .field-description {
      padding-bottom: 2rem;
      padding-left: 0.25rem;
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

  .form-group {
    .help-icon > img {
      bottom: 10px !important;
    }
  }
}
</style>
