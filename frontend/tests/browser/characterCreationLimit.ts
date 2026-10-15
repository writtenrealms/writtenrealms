// Real lobby and creation components with local responses; no accounts or worlds are changed.
import { createApp, nextTick } from 'vue';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';
import axios from 'axios';
import lobby from '../../src/store/modules/lobby';
import WorldLobby from '../../src/views/lobby/WorldLobby.vue';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
const used = params.has('available') ? 0 : 1;
const char = (id: number, name: string, is_builder: boolean) => ({
  id, name, is_builder, level: 1, archetype: 'hoplite', gender: 'male',
  last_connection_ts: new Date().toISOString(), world_is_multi: true,
});
const chars = [char(1, 'Builder', true), ...(used ? [char(2, 'Alexios', false)] : [])];
const world = {
  id: 12, name: 'Phalanx', description: 'A world of ancient Greece. Closed alpha.',
  instance_of: {}, is_multiplayer: true, allow_combat: true, can_create_chars: true,
  can_edit: false, character_creation_limit: { maximum: 1, used },
  is_classless: true, can_select_gender: true, core_factions: [], player_creation: {},
};
axios.defaults.adapter = async config => {
  let data: any;
  let status = 200;
  if (config.method === 'post') {
    if (params.has('stale')) throw { response: { data: {
      code: 'character_limit_reached', detail: 'This world allows 1 character per account. You have reached the limit. Builder characters do not count.',
    } } };
    data = char(3, JSON.parse(config.data).name, false);
    status = 201;
  } else if (config.url!.includes('leaderboards')) data = { panels: [] };
  else if (config.url!.includes('/chars/')) data = { results: chars };
  else data = world;
  return { data, status, statusText: 'OK', config, headers: {} };
};
const store = createStore({
  modules: { lobby }, state: { auth: { user: { is_temporary: false } } },
  getters: { isAuthenticated: () => true },
});
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/worlds/:world_id', name: 'lobby_world_details', component: WorldLobby },
  { path: '/characters/:player_id', name: 'character_details', component: {} },
] });
await router.push('/worlds/12' + (params.has('deep') ? '?create=1' : ''));
createApp(WorldLobby).use(store).use(router).mount('#app');
await new Promise(resolve => setTimeout(resolve, 150));
await nextTick();
const notice = document.querySelector('[role="status"]')?.textContent || '';
const buttons = Array.from(document.querySelectorAll('button'));
const create = buttons.find(button => button.textContent === 'NEW CHARACTER');
if (used && (!create?.disabled || !notice.includes('Builder characters do not count') || document.querySelector('#lobby-new-character'))) {
  throw new Error('Cap/deep-link enforcement failed');
}
if (!used && create?.disabled) throw new Error('Builder consumed a slot');
document.querySelector('#results')!.textContent = 'PASS: lobby slot availability and deep-link checks';
