// Exercise the real store and console with local events; no gameplay is sent.
import { createApp, nextTick } from 'vue';
import { createStore } from 'vuex';
import router from '../../src/router';
import game from '../../src/store/modules/game';
import Console from '../../src/components/game/console/Console.vue';
import { deathArrival } from '../fixtures/deathArrival.mjs';
import '../../src/styles/app.scss';

router.push = async () => {};
const mobile = new URLSearchParams(location.search).has('mobile');
document.querySelector('#app')!.classList.toggle('mobile', mobile);
let receive: (event: { data: string }) => void;
const commands: string[] = [];
const store = createStore({ modules: { game: {
  ...game,
  state: {
    ...structuredClone(game.state), is_mobile: mobile, player_config: {},
    player: { id: 3, key: 'player.3' },
    world: { id: 26, instance_ref: 'battlefield-run' },
    map: { 'room.99': { id: 99, key: 'room.99' } },
  },
  mutations: { ...game.mutations, openWS: (_state, callbacks) => { receive = callbacks.onmessage; } },
  actions: { ...game.actions, cmd: (_context, command) => { commands.push(command); } },
} } });
createApp(Console).use(store).mount('#app');

async function run() {
  await store.dispatch('game/openWebSocket');
  const { death, sync, recovery } = deathArrival();
  for (const message of [death, sync, recovery]) receive({ data: JSON.stringify(message) });
  await nextTick();
  await new Promise(requestAnimationFrame);
  const rooms = document.querySelectorAll('.room-name');
  if (rooms.length !== 1 || rooms[0].textContent !== 'The Underworld') {
    throw new Error('Expected exactly one destination room');
  }
  const state = store.state.game;
  if (state.world.id !== 13 || state.map['room.99'] || !state.map['room.7']) {
    throw new Error('Destination world/map was not restored');
  }
  if (state.last_viewed_room_message !== state.messages[0]) {
    throw new Error('The visible death room lost its controls');
  }
  const button = document.querySelector<HTMLButtonElement>('.room-actions button');
  if (!button) throw new Error('Room action is missing');
  button.click();
  if (commands.join(',') !== 'touch stone') throw new Error('Room action sent the wrong command');
  if (document.documentElement.scrollWidth > innerWidth) throw new Error('Horizontal overflow');
  document.querySelector('#results')!.textContent =
    `PASS: ${mobile ? 'mobile' : 'desktop'} death arrival; one room, restored map, working room action`;
}
run().catch(error => {
  document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
  console.error(error);
});
