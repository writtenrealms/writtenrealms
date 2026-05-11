<template>
  <div id="input">
    <form @submit.prevent="onSubmit">
      <div class="form-group">
        <input
          id="console-input"
          type="text"
          v-model="input"
          @blur="onBlur"
          @focus="onFocus"
          @keydown.tab="onTab"
          autocomplete="off"
          autocorrect="off"
          autocapitalize="off"
          spellcheck="false"
        />
      </div>
    </form>
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { useStore } from 'vuex';
import { getMovementDirectionFromArrowKey } from '@/core/keyboard';

const store = useStore();

const input = ref("");
const focused = ref(false);

const communicationCommands = [
  'CHAT', 'CHA', 'CH', 'TELL', 'TEL', 'TE', 'REPLY', 'REPL', 'REP', 'SAY', 'SA',
  'CCHAT', 'CCHA', 'CCH', 'CC', 'GOSSIP', 'GOSSI', 'GOSS', 'GOS', 'GO'
];

let last_sent = '';
const onSubmit = () => {

  let user_input = '';

  if (input.value) {
    user_input = input.value;

    const first_token = user_input.split(' ')[0].toUpperCase();
    if (!communicationCommands.includes(first_token)) {
      last_sent = user_input
    }
  } else if (last_sent) {
    user_input = last_sent;
  }

  if (user_input) {
    store.dispatch("game/cmd", user_input);
  }

  input.value = '';
};

const onKeyDown = (event: KeyboardEvent) => {
  event.stopPropagation();

  if (store.state.ui.modal.is_open) {
    return;
  }

  if (input.value && focused.value) return;

  const direction = getMovementDirectionFromArrowKey(event);
  if (!direction) return;

  event.preventDefault();
  store.dispatch("game/cmd", direction);
};

const onBlur = () => {
  focused.value = false;
};

const onFocus = () => {
  focused.value = true;
};

const onTab = (event: KeyboardEvent) => {
  if (!input.value) {
    return;
  }

  event.preventDefault();

  const tokens = input.value.split(/\s+/);
  const lastToken = tokens[tokens.length - 1].toLowerCase();

  // If there's only one word in the input bar, then assume a command
  // is being tab-completed using known abilities.
  if (tokens.length === 1) {
    const playerState = store.state.game.player || {};
    const worldState = store.state.game.world || {};
    const playerAbilities = playerState.known_abilities || [];
    const worldAbilities = worldState.abilities?.definitions || {};

    const commands: string[] = [];
    for (const slug of playerAbilities) {
      const ability = worldAbilities[slug];
      if (!ability) continue;

      const verbs = ability.command_verbs || [];
      commands.push(verbs[0] || ability.slug || slug);
    }

    for (const command of commands) {
      if (command.match("^" + lastToken)) {
        input.value = command;
        return;
      }
    }
    return;
  }

  // Otherwise, assume we are trying to target a mob, an item in the room,
  // or an item in the player's inventory .

  // Add the room's characters
  let things = store.state.game.room.chars || [];
  // Add the player's inventory
  things = things.concat(store.state.game.player.inventory);
  // Add the room's inventory
  things = things.concat(store.state.game.room.inventory);

  let replacement;
  for (const thing of things) {
    const keywords = thing.keywords.split(/\s+/);
    for (let keyword of keywords) {
      keyword = keyword.toLowerCase();
      if (keyword.match("^" + lastToken)) {
        replacement = keyword;
        break;
      }
    }
    if (replacement) break;
  }

  if (replacement) {
    tokens.splice(tokens.length - 1, 1, replacement);
    input.value = tokens.join(" ");
  }
};

onMounted(() => {
  window.addEventListener("keydown", onKeyDown);
  const inputEl = document.getElementById("console-input") as HTMLElement;
  inputEl.focus();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeyDown);
});
</script>

<style lang="scss">
</style>
