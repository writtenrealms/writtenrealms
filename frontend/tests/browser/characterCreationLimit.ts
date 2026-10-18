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
  instance_of: {}, is_multiplayer: true, allow_combat: true, can_create_chars: !params.has('closed'),
  can_edit: params.has('builder'), character_creation_limit: { maximum: 1, used },
  is_classless: true, can_select_gender: true, core_factions: [], player_creation: {},
};
axios.defaults.adapter = async config => {
  let data: any;
  let status = 200;
  if (config.method === 'post') {
    if (params.has('stale')) throw { response: { data: {
      code: 'character_limit_reached', detail: 'This world allows 1 character per account. You have reached the limit.',
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
  { path: '/worlds/:world_id/about', name: 'lobby_world_information', component: {} },
  { path: '/build/worlds/:world_id', name: 'builder_world_index', component: {} },
  { path: '/characters/:player_id', name: 'character_details', component: {} },
] });
await router.push('/worlds/12' + (params.has('deep') ? '?create=1' : ''));
createApp(WorldLobby).use(store).use(router).mount('#app');
await new Promise(resolve => setTimeout(resolve, 150));
await nextTick();
const notice = document.querySelector('[role="status"]')?.textContent || '';
const buttons = Array.from(document.querySelectorAll('button'));
const create = buttons.find(button => button.textContent === 'NEW CHARACTER');
const opensFromLink = !used && !params.has('closed') && params.has('deep');
const information = document.querySelector('.world-information-link')!.getBoundingClientRect();
const description = document.querySelector('.world-description .desc-line:last-of-type')!.getBoundingClientRect();
if (information.top < description.bottom || information.top - description.bottom > 16 || Math.abs(information.left - description.left) > 1) {
  throw new Error('More information must sit directly below and align with the world description');
}
if ((!opensFromLink && !create) || create?.disabled || notice) {
  throw new Error('New Character must remain interactive without showing a notice initially');
}
if (opensFromLink && !document.querySelector('#lobby-new-character')) throw new Error('Available creation link did not open the form');
if ((used || params.has('closed')) && document.querySelector('#lobby-new-character')) {
  throw new Error('Cap/deep-link enforcement failed');
}
if (params.has('exercise') && create) {
  create.click();
  await nextTick();
  const explanation = document.querySelector('[role="status"]')?.textContent;
  if (used || params.has('closed')) {
    const expected = params.has('closed') ? 'Character creation is disabled for this world.'
      : 'This world allows 1 character per account. You have reached the limit.';
    if (explanation !== expected || document.querySelector('#lobby-new-character')) {
      throw new Error('Blocked creation must explain the limit only after interaction');
    }
    await router.push('/worlds/13');
    await new Promise(resolve => setTimeout(resolve, 150));
    await nextTick();
    if (document.querySelector('[role="status"]')) throw new Error('Notice survived world navigation');
  } else if (!document.querySelector('#lobby-new-character') || explanation) {
    throw new Error('Builder consumed a slot or prevented ordinary creation');
  }
}
document.querySelector('#results')!.textContent = 'PASS: lobby availability, interaction, and deep-link checks';
