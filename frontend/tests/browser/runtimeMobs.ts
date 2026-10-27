import { createApp, h, nextTick } from 'vue';
import { createRouter, createMemoryHistory, RouterView } from 'vue-router';
import { createStore } from 'vuex';
import { setLucideProps } from '@lucide/vue';
import axios from 'axios';
import Header from '../../src/components/Header.vue';
import BuilderFrame from '../../src/components/builder/BuilderFrame.vue';
import AdminInstance from '../../src/views/builder/world/AdminInstance.vue';
import RuntimeMobs from '../../src/views/builder/world/RuntimeMobs.vue';
import RuntimeMobDebug from '../../src/views/builder/world/RuntimeMobDebug.vue';
import { rootWorld, worldInstance } from '../fixtures/worldAdmin.mjs';
import { mob, runtime, preview } from '../fixtures/runtimeMobs.mjs';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
if (params.has('capture')) document.querySelector<HTMLElement>('#results')!.style.display = 'none';
const calls: any[] = [];
let missing = false;
let empty = params.has('empty');
const mobPath = '/build/worlds/12/admin/13/mobs/123';
const api = '/builder/worlds/12/admin/instance/13/mobs/';
axios.defaults.adapter = async config => {
  calls.push({ url: config.url, method: config.method, data: config.data });
  const responses: Record<string, any> = { '/builder/worlds/12/': rootWorld, '/builder/worlds/12/map/': { rooms: {} }, '/lobby/config/': { main_world_id: 12, building_enabled: true },
    [api]: { world: { ...runtime, lifecycle: empty ? 'stopped' : 'running' }, results: empty ? [] : [mob], next_after: null },
    [`${api}123/`]: { world: runtime, mob }, [`${api}123/debug/`]: { mob, world_conversation: { knowledge: 'The Agora is a marketplace.' }, policy: { enabled: true, world_id: 12 }, focus: null, combat: [], inspected_at: runtime.inspected_at } };
  if (missing && config.url?.startsWith(api)) throw Object.assign(new Error('Missing'), { response: { status: 404 } });
  if (!(config.url! in responses)) throw new Error(`Unexpected fixture request: ${config.url}`);
  const data = config.method === 'post' ? preview(JSON.parse(config.data).speech) : responses[config.url!];
  return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
};
const store = createStore({
  state: { auth: { user: { id: 1, is_staff: !params.has('builder') } }, builder: { world: rootWorld, map: {}, room: null, zone: null, worlds: { admin: { admin_instance: worldInstance() } } } },
  getters: { isAuthenticated: () => true },
  mutations: { 'builder/reset_state': () => {}, 'builder/world_set': (s, w) => { s.builder.world = w; }, 'builder/map_set': (s, m) => { s.builder.map = m; }, 'ui/notification_set': () => {} },
  actions: { 'builder/worlds/admin/world_admin_instance_fetch': () => {} },
});
const destination = { template: '<p>Preview destination</p>' };
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/build/worlds/:world_id', component: BuilderFrame, children: [
    { path: 'admin/:instance_id', name: 'builder_world_admin_instance', component: AdminInstance },
    { path: 'admin/:instance_id/mobs/:mob_id/debug', name: 'builder_world_admin_mob_debug', component: RuntimeMobDebug },
    { path: 'admin/:instance_id/mobs/:mob_id?', name: 'builder_world_admin_mobs', component: RuntimeMobs },
  ] },
  ...['builder_world_index', 'lobby_world_details', 'builder_mob_definition_list', 'builder_item_definition_list', 'builder_world_quest_template_list', 'builder_world_config', 'builder_world_edit', 'builder_zone_list', 'builder_world_admin', 'builder_world_builder_list', 'builder_world_player_list', 'builder_world_faction_list', 'builder_world_export', 'builder_world_player_details', 'staff_index', 'lobby', 'lobby_building'].map(name => ({ path: `/${name}/:world_id?/:player_id?`, name, component: destination })),
] });
setLucideProps({ size: 18, strokeWidth: 1.75 });
await router.push(params.has('test') ? '/build/worlds/12/admin/13' : params.has('empty') ? '/build/worlds/12/admin/13/mobs' : params.has('debug') ? `${mobPath}/debug` : mobPath);
await router.isReady();
createApp({ render: () => h('div', [h(Header), h('div', { id: 'main' }, h(RouterView))]) }).use(store).use(router).mount('#app');
const frame = async () => { await nextTick(); await new Promise(requestAnimationFrame); await nextTick(); await new Promise(requestAnimationFrame); };
const button = (text: string) => Array.from(document.querySelectorAll('button')).find(el => el.textContent?.trim() === text)!;
const text = () => document.querySelector('#app')!.textContent!;
let checks = 0;
function check(condition: boolean, message: string) { if (!condition) throw new Error(message); checks++; }
function layout() {
  check(document.documentElement.scrollWidth <= innerWidth, 'Page overflows horizontally');
  for (const el of document.querySelectorAll('.panel, .json-inspector')) check(el.scrollWidth <= el.clientWidth, 'Panel content overflows');
}
async function run() {
  await frame();
  (document.querySelector('.metric-card-link') as HTMLElement).click(); await frame(); await frame();
  check(text().includes('Loaded mobs') && text().includes('Menon'), 'Mobs metric did not open list');
  (document.querySelector('.mob-list a') as HTMLElement).click(); await frame();
  check(text().includes('Approved knowledge'), 'Mob selection did not show details'); layout();
  (document.querySelector(`a[href="${mobPath}/debug"]`) as HTMLElement).click(); await frame();
  check(text().includes('LLM payload preview'), 'Debug navigation failed');
  const input = document.querySelector('textarea')!;
  input.value = 'Can an agreed price still be unfair?'; input.dispatchEvent(new Event('input', { bubbles: true })); await frame();
  const before = calls.filter(c => c.method === 'post').length;
  button('BUILD PAYLOAD PREVIEW').click(); await frame();
  check(calls.filter(c => c.method === 'post').length === before + 1, 'Preview did not send exactly one reconstruction request');
  check(text().includes('Reconstructed request') && text().includes('Not sent') && text().includes('max_output_tokens'), 'Payload missing'); layout();
  input.value = 'A different thought'; input.dispatchEvent(new Event('input', { bubbles: true })); await frame();
  check(!text().includes('Reconstructed request'), 'Editing speech left stale preview visible');
  button('BUILD PAYLOAD PREVIEW').click(); await frame();
  button('REFRESH').click(); await frame();
  check(!text().includes('Reconstructed request'), 'Refreshing left stale preview visible');
  missing = true; button('REFRESH').click(); await frame();
  check(text().includes('no longer available') && !text().includes('Current raw records'), 'Despawn left stale data visible');
  missing = false; empty = true; await router.push('/build/worlds/12/admin/13/mobs'); await frame();
  check(text().includes('This world is stopped'), 'Stopped-world explanation missing'); layout();
  document.querySelector('#results')!.textContent = `PASS: ${checks} mob inspector checks at ${innerWidth} × ${innerHeight}`;
}
if (params.has('test')) void run().catch(error => { document.querySelector('#results')!.textContent = `FAIL: ${error.message}`; console.error(error); });
