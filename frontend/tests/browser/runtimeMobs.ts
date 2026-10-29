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
import { mob, runtime, preview, failure } from '../fixtures/runtimeMobs.mjs';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
if (params.has('capture')) document.querySelector<HTMLElement>('#results')!.style.display = 'none';
const calls: any[] = [];
let missing = false;
let empty = params.has('empty');
const mobPath = '/build/worlds/12/admin/13/mobs/123';
const api = '/builder/worlds/12/admin/instance/13/mobs/';
const listedMob = (id: number, name: string, roomName: string, zone: string, zoneId: number) => ({
  ...mob, id, name, room: { ...mob.room, name: roomName, zone, zone_id: zoneId },
});
const firstPage = [
  mob,
  listedMob(124, 'a practice dummy', 'Exercise Room', 'Athens - Gymnasium', 2),
  listedMob(125, 'Priestess Callista', 'Altar of Demeter', 'Athens - Agora', 1),
  listedMob(126, 'a sparabara', 'North Holding Cell', 'Persian Outpost', 3),
  listedMob(127, 'a linen seller', 'Agora Southeast Stoa', 'Athens - Agora', 1),
  listedMob(128, 'Demeas the Hoplomachos', 'Wrestling Area', 'Athens - Gymnasium', 2),
];
// Distinct zone IDs must not merge even when builders give them identical names.
const secondPage = [
  listedMob(129, 'a guard', 'Gate', 'Borderlands', 4),
  listedMob(130, 'a sentry', 'Watchtower', 'Borderlands', 5),
];
axios.defaults.adapter = async config => {
  calls.push({ url: config.url, method: config.method, data: config.data });
  const query = String(config.params?.q || '').toLowerCase();
  const page = config.params?.after ? secondPage : firstPage;
  const results = empty ? [] : page.filter(entry => `${entry.name} ${entry.id} ${entry.room.name}`.toLowerCase().includes(query));
  const responses: Record<string, any> = { '/builder/worlds/12/': rootWorld, '/builder/worlds/12/map/': { rooms: {} }, '/lobby/config/': { main_world_id: 12, building_enabled: true },
    [api]: { world: { ...runtime, lifecycle: empty ? 'stopped' : 'running' }, results, next_after: !empty && !query && !config.params?.after && params.has('test') ? 128 : null },
    [`${api}123/`]: { world: runtime, mob }, [`${api}123/debug/`]: { mob, conversation_failure: params.has('failure') || params.has('test') ? failure : null, world_conversation: { knowledge: 'The Agora is a marketplace.' }, policy: { enabled: true, world_id: 12 }, focus: null, combat: [], inspected_at: runtime.inspected_at } };
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
const destination = { render: () => h('p', 'Preview destination') };
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/build/worlds/:world_id', component: BuilderFrame, children: [
    { path: 'mobs/:mob_definition_id', name: 'builder_mob_definition_details', component: destination },
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
  const entry = document.querySelector('.mob-list a')!;
  const summary = entry.querySelector<HTMLElement>('.mob-list-summary')!;
  check(summary.textContent === `${mob.id} - ${mob.room.name}`, 'Mob list ID and room summary is incorrect');
  check(entry.querySelectorAll('span').length === 1 && !entry.textContent!.includes('Level'), 'Mob list repeats metadata');
  check(getComputedStyle(summary).whiteSpace === 'nowrap', 'Mob summary must stay on one line');
  const groups = () => Array.from(document.querySelectorAll('.mob-zone'));
  check(groups().map(group => group.querySelector('h2')!.textContent).join('|') === 'Athens - Agora|Athens - Gymnasium|Persian Outpost', 'Zone groups are not alphabetized');
  check(groups().map(group => group.querySelector('.tag')!.textContent).join('|') === '3|2|1', 'Zone counts are incorrect');
  check(groups()[0].querySelectorAll('li').length === 3, 'Nonadjacent mobs in the same zone were not grouped');
  layout();
  const zoneOpen = (index: number) => (groups()[index] as HTMLDetailsElement).open;
  check(groups().every(group => (group as HTMLDetailsElement).open), 'Zone groups should start expanded');
  const beforeToggle = calls.length;
  (groups()[1].querySelector('summary') as HTMLElement).click(); await frame();
  check(!zoneOpen(1) && zoneOpen(0) && zoneOpen(2), 'Collapsing a zone changed another group');
  check(groups()[1].querySelector('.tag')!.getBoundingClientRect().height > 0, 'Collapsed group lost its count');
  check(!groups()[1].querySelector('a')!.checkVisibility(), 'Collapsed mobs remain visible');
  check(calls.length === beforeToggle, 'Collapsing a zone sent a request');
  button('REFRESH').click(); await frame();
  check(!zoneOpen(1), 'Refresh discarded collapsed zones');
  button('NEXT 50').click(); await frame();
  check(groups().length === 2 && groups().every(group => group.querySelector('h2')!.textContent === 'Borderlands'), 'Distinct zones with the same name were merged');
  check(!text().includes('Priestess Callista') && text().includes('2 shown on this page'), 'Pagination retained stale groups or misleading counts');
  button('PREVIOUS').click(); await frame();
  check(groups().length === 3, 'Previous page did not restore zone groups');
  check(!zoneOpen(1), 'Pagination discarded collapsed zones');
  (groups()[0].querySelector('summary') as HTMLElement).click(); await frame();
  const search = document.querySelector<HTMLInputElement>('#mob-search')!;
  search.value = 'Menon'; search.dispatchEvent(new Event('input', { bubbles: true })); await frame();
  button('SEARCH').click(); await frame();
  check(groups().length === 1 && groups()[0].querySelector('.tag')!.textContent === '1', 'Search groups/counts did not update');
  check(zoneOpen(0), 'New search should reveal matching mobs');
  search.value = 'does not exist'; search.dispatchEvent(new Event('input', { bubbles: true })); await frame();
  button('SEARCH').click(); await frame();
  check(groups().length === 0 && text().includes('No mobs match this search.'), 'Empty search left stale zone headings');
  search.value = ''; search.dispatchEvent(new Event('input', { bubbles: true })); await frame();
  button('SEARCH').click(); await frame();
  (groups()[1].querySelector('summary') as HTMLElement).click(); await frame();
  const listCalls = calls.filter(call => call.url === api).length;
  (document.querySelector('.mob-list a') as HTMLElement).click(); await frame();
  check(!zoneOpen(1), 'Mob selection discarded collapsed zones');
  check(calls.filter(call => call.url === api).length === listCalls, 'Mob selection unnecessarily reloaded the list');
  check(text().includes('Approved knowledge'), 'Mob selection did not show details'); layout();
  const definitionLink = document.querySelector<HTMLAnchorElement>('.inspection-facts a')!;
  check(definitionLink.textContent === mob.definition.slug, 'Definition link label is incorrect');
  check(definitionLink.getAttribute('href') === '/build/worlds/12/mobs/35', 'Definition link points to the runtime world or mob');
  definitionLink.click(); await frame();
  check(router.currentRoute.value.name === 'builder_mob_definition_details' && text().includes('Preview destination'), 'Definition navigation failed');
  await router.push(mobPath); await frame();
  (document.querySelector(`a[href="${mobPath}/debug"]`) as HTMLElement).click(); await frame();
  check(text().includes('LLM payload preview'), 'Debug navigation failed');
  check(text().includes('Latest dialogue failure') && text().includes('forbidden_speech_characters'), 'Failure reason missing');
  check(text().includes('Rejected response') && text().includes('U+000A') && text().includes('\\nWhat would convince you?'), 'Rejected response evidence missing');
  layout();
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
  check(text().includes('no longer available') && !text().includes('Current raw records') && !text().includes('Rejected response'), 'Despawn left stale data visible');
  missing = false; empty = true; await router.push('/build/worlds/12/admin/13/mobs'); await frame();
  button('REFRESH').click(); await frame();
  check(text().includes('This world is stopped'), 'Stopped-world explanation missing'); layout();
  document.querySelector('#results')!.textContent = `PASS: ${checks} mob inspector checks at ${innerWidth} × ${innerHeight}`;
}
if (params.has('test')) void run().catch(error => { document.querySelector('#results')!.textContent = `FAIL: ${error.message}`; console.error(error); });

// Open ?hover=1, then hover the selected mob to check the actual link cascade.
if (params.has('hover')) {
  await frame(); await frame();
  const entry = document.querySelector<HTMLElement>('.mob-list a')!;
  const elements = [entry, ...entry.querySelectorAll('*')];
  const colors = elements.map(el => getComputedStyle(el).color);
  entry.addEventListener('pointerenter', async () => {
    await frame();
    try {
      check(entry.matches(':hover'), 'Mob entry is not hovered');
      elements.forEach((el, index) => {
        check(getComputedStyle(el).textDecorationLine === 'none', 'Mob entry is underlined');
        check(getComputedStyle(el).color === colors[index], 'Mob entry changes text color on hover');
      });
      document.querySelector('#results')!.textContent = `PASS: ${checks} mob list hover checks`;
    } catch (error) {
      document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
    }
  }, { once: true });
}
