// Actual builder frame and runtime page; all data and actions stay in memory.
import { createApp, h, nextTick } from 'vue';
import { createRouter, createMemoryHistory, RouterView } from 'vue-router';
import { createStore } from 'vuex';
import { setLucideProps } from '@lucide/vue';
import axios from 'axios';
import Header from '../../src/components/Header.vue';
import BuilderFrame from '../../src/components/builder/BuilderFrame.vue';
import AdminInstance from '../../src/views/builder/world/AdminInstance.vue';
import { rootWorld, worldInstance } from '../fixtures/worldAdmin.mjs';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
if (params.has('capture')) document.querySelector<HTMLElement>('#results')!.style.display = 'none';
const instance = worldInstance();
if (params.has('long')) {
  instance.world_state = { ['long_key_'.repeat(15)]: 'A long state value '.repeat(30) };
  instance.active_players[0].name = 'ACharacterWithAnExceptionallyLongName';
  instance.active_players[0].room.name = 'A very long room name '.repeat(10);
}
if (params.has('empty')) { instance.active_players = []; instance.world_state = {}; instance.counts.players_logged_in = 0; }
let refreshes = 0;
let resets = 0;
let recoveries = 0;
axios.defaults.adapter = async config => {
  const responses = { '/builder/worlds/12/': rootWorld, '/builder/worlds/12/map/': { rooms: {} }, '/lobby/config/': { main_world_id: 12, building_enabled: true } };
  if (config.method !== 'get' || !(config.url! in responses)) throw new Error(`Unexpected preview request: ${config.url}`);
  return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(responses[config.url!]) };
};
const store = createStore({
  state: { auth: { user: { id: 1, is_staff: true } }, builder: { world: rootWorld, map: {}, room: null, zone: null, worlds: { admin: { admin_instance: instance } } } },
  getters: { isAuthenticated: () => true },
  mutations: {
    'builder/reset_state': () => {}, 'builder/world_set': (state, world) => { state.builder.world = world; },
    'builder/map_set': (state, map) => { state.builder.map = map; }, 'ui/notification_set': () => {},
  },
  actions: {
    'builder/worlds/admin/world_admin_instance_fetch': async () => { refreshes++; },
    'builder/worlds/admin/world_admin_instance_reset': async () => { resets++; },
    'builder/worlds/admin/world_admin_instance_recover': async () => { recoveries++; },
  },
});
const destination = { template: '<p>Preview destination</p>' };
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/build/worlds/:world_id', component: BuilderFrame, children: [{ path: 'admin/:instance_id', name: 'builder_world_admin_instance', component: AdminInstance }] },
  ...['builder_world_index', 'lobby_world_details', 'builder_mob_definition_list', 'builder_item_definition_list', 'builder_world_quest_template_list', 'builder_world_config', 'builder_world_edit', 'builder_zone_list', 'builder_world_admin', 'builder_world_builder_list', 'builder_world_player_list', 'builder_world_faction_list', 'builder_world_export', 'builder_world_player_details', 'staff_index', 'lobby', 'lobby_building'].map(name => ({ path: `/${name}/:world_id?/:player_id?`, name, component: destination })),
] });
setLucideProps({ size: 18, strokeWidth: 1.75 });
await router.push('/build/worlds/12/admin/13');
await router.isReady();
createApp({ render: () => h('div', [h(Header), h('div', { id: 'main' }, h(RouterView))]) }).use(store).use(router).mount('#app');
const frame = async () => { await nextTick(); await new Promise(requestAnimationFrame); await nextTick(); };
const button = (text: string) => Array.from(document.querySelectorAll('button')).find(el => el.textContent?.trim() === text)!;
let checks = 0;
function check(condition: boolean, message: string) { if (!condition) throw new Error(message); checks++; }
async function run() {
  await frame(); await frame();
  check(document.documentElement.scrollWidth <= innerWidth, 'Page overflows horizontally');
  check(document.querySelectorAll('.metric-card').length === 6, 'Metrics missing');
  for (const el of document.querySelectorAll('.metric-card, .dashboard-section')) {
    check(getComputedStyle(el).backgroundImage === 'none', 'Unexpected panel gradient');
    check(el.scrollWidth <= el.clientWidth, 'Panel content overflows');
  }
  const before = refreshes;
  button('REFRESH').click(); await frame();
  check(refreshes === before + 1, 'Refresh action did not fetch exactly once');
  check(!button('RESET WORLD') && !button('RECOVER'), 'Running world exposed cleanup actions');
  const originalConfirm = window.confirm;
  try {
    store.state.builder.worlds.admin.admin_instance.lifecycle_details.current = 'stopped'; await frame();
    check(!!button('RESET WORLD'), 'Stopped world has no reset action');
    window.confirm = () => false;
    button('RESET WORLD').click(); await frame();
    check(resets === 0, 'Cancelled reset dispatched');
    window.confirm = () => true;
    button('RESET WORLD').click(); await frame();
    check(resets === 1, 'Confirmed reset did not dispatch');
    store.state.builder.worlds.admin.admin_instance.lifecycle_details.current = 'failed';
    store.state.builder.worlds.admin.admin_instance.recovery_actions.recover_to_stopped = true; await frame();
    check(!!document.querySelector('.dashboard-heading .tag-red'), 'Failure status not visible');
    window.confirm = () => false;
    button('RECOVER').click(); await frame();
    check(recoveries === 0, 'Cancelled recovery dispatched');
    window.confirm = () => true;
    button('RECOVER').click(); await frame();
    check(recoveries === 1, 'Confirmed recovery did not dispatch');
  } finally { window.confirm = originalConfirm; }
  store.state.builder.worlds.admin.admin_instance.lifecycle_details.current = 'running';
  store.state.builder.worlds.admin.admin_instance.recovery_actions.recover_to_stopped = false; await frame();
  document.querySelector('#results')!.textContent = `PASS: ${checks} world admin checks at ${innerWidth} × ${innerHeight}`;
  // Use ?test=1&hover=1, then move the pointer onto the loaded-mobs card.
  // Check the actual cascade, including the global a:hover rule and inherited color.
  if (params.has('hover')) {
    const card = document.querySelector<HTMLElement>('.metric-card-link')!;
    const elements = [card, ...card.querySelectorAll('*')];
    const colors = elements.map(el => getComputedStyle(el).color);
    const border = getComputedStyle(card).borderColor;
    card.addEventListener('pointerenter', async () => {
      await frame();
      try {
        check(card.matches(':hover'), 'Card is not actually hovered');
        check(getComputedStyle(card).borderColor !== border, 'Hover border did not change');
        check(getComputedStyle(card).borderColor === getComputedStyle(card.querySelector('.lucide-arrow-right')!).color, 'Hover border is not gold');
        elements.forEach((el, index) => {
          check(getComputedStyle(el).textDecorationLine === 'none', 'Hovered card text is underlined');
          check(getComputedStyle(el).color === colors[index], 'Hovered card text changed color');
        });
        document.querySelector('#results')!.textContent = `PASS: ${checks} world admin checks, including card hover`;
      } catch (error) {
        document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
      }
    }, { once: true });
  }
}
if (params.has('test')) void run().catch(error => { document.querySelector('#results')!.textContent = `FAIL: ${error.message}`; console.error(error); });
