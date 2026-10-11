// Real zone and preview components with synthetic data and no backend traffic.
import { createApp, h } from 'vue';
import { createRouter, createMemoryHistory } from 'vue-router';
import { createStore } from 'vuex';
import { setLucideProps } from '@lucide/vue';
import axios from 'axios';
import Zone from '../../src/views/builder/zone/Zone.vue';
import '../../src/styles/app.scss';
import 'floating-vue/dist/style.css';
import '../../src/styles/floating-vue-custom.scss';

const zone = { id: 2, relative_id: 2, name: 'Battlefield', respawn: { mode: 'fixed', seconds: 300 }, door_reset: { mode: 'fixed', seconds: 300 } };
const rooms = Array.from({ length: 15 }, (_, i) => ({
  id: i + 1, key: `room.${i + 1}`, relative_id: i + 1, name: 'The battlefield', zone,
  x: i % 5, y: Math.floor(i / 5), z: 0, type: 'field', flags: [],
  east: i % 5 < 4 ? `room.${i + 2}` : null, west: i % 5 ? `room.${i}` : null,
  north: i < 10 ? `room.${i + 6}` : null, south: i >= 5 ? `room.${i - 4}` : null,
}));
const map = Object.fromEntries(rooms.map(room => [room.key, room]));
const mob = {
  id: 1, name: 'a Persian spearman', slug: 'persian-spearman', type: 'humanoid',
  description: 'Dust settles in the folds of his linen tunic. He carries a long ash spear and a wicker shield faced with hide.',
  room_description: 'A Persian spearman watches the battlefield from behind his shield.',
  base_properties: { level: 5, health_max: 80, energy_max: 40, armor: 12 },
  attributes: { strength: 12, dexterity: 8, constitution: 10 },
};
const names = ['a Persian spearman', 'a Persian archer', 'a Persian captain', 'a wounded soldier'];
const mobs = Object.fromEntries(names.map((name, i) => [i + 1, { ...mob, id: i + 1, name, slug: name.slice(2).replaceAll(' ', '-') }]));
const entries = names.map((name, i) => ({
  slug: `entry-${i}`, name: '', count: i ? 1 : 6, is_active: true, conditional: false,
  target: `room@${i + 4}`, target_name: 'The battlefield', pooled: false,
  sources: [{ ref: `mobdefinition.${mobs[i + 1].slug}`, name, mob_id: i + 1, weight: 1 }],
}));
const data = { mobs, entries: [
  ...entries,
  { ...entries[0], slug: 'patrol', count: { min: 2, max: 4 }, pooled: true, target: 'path@1', target_name: 'Patrol route', sources: [
    { ...entries[0].sources[0], weight: 70 }, { ...entries[1].sources[0], weight: 30 },
  ] },
  { ...entries[2], slug: 'reserve', is_active: false, conditional: true },
  { ...entries[0], slug: 'supplies', count: 2, sources: [{ ref: 'itembundle.supplies', name: 'Battlefield supplies', weight: 1 }] },
] };
const plans = [
  { id: 1, slug: 'battlefield', name: 'Battlefield Spawns', num_entries: 7, respawn_mode: 'none', is_active: true },
  { id: 2, slug: 'empty', name: 'Empty plan', num_entries: 0, is_active: false },
];
const requests: string[] = [];
const failFirst = new URLSearchParams(location.search).has('failure');
let failures = 0;
(window as any).previewRequests = requests;
axios.defaults.adapter = async config => {
  requests.push(config.url!);
  await new Promise(resolve => setTimeout(resolve, 80));
  if (config.method !== 'get') throw new Error('This preview is read-only.');
  let payload;
  if (config.url!.endsWith('/spawn-plans/')) payload = { spawn_plans: plans };
  else if (config.url!.endsWith('/spawn-plans/1/')) {
    if (failFirst && failures++ === 0) throw new Error('Simulated loading failure.');
    payload = data;
  } else if (config.url!.endsWith('/spawn-plans/2/')) payload = { entries: [], mobs: {} };
  else throw new Error(`Unexpected preview request: ${config.url}`);
  return { config, data: structuredClone(payload), status: 200, statusText: 'OK', headers: {} };
};
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/build/worlds/:world_id/zones/:zone_relative_id', name: 'builder_zone_index', component: Zone },
  ...['builder_zone_config', 'builder_zone_spawn_plan_details', 'builder_mob_definition_details'].map(name => ({
    path: `/${name}/:world_id/:zone_relative_id?/:spawn_plan_id?/:mob_definition_id?`, name, component: { template: '<p>Preview destination</p>' },
  })),
] });
const store = createStore({ state: { builder: { zone, room: rooms[7], map, world: { builder_info: { builder_rank: 3 } } }, game: { player: null } },
  actions: { 'builder/zone_rooms_fetch': () => rooms },
  mutations: {
    'builder/zone_rooms_set': () => {}, 'builder/map_add': () => {},
    'builder/room_set': (state, room) => {
      state.builder.room = room;
      (window as any).previewSelectedRoom = room.key;
    },
  },
});
setLucideProps({ size: 18, strokeWidth: 1.75 });
await router.push('/build/worlds/15/zones/2');
await router.isReady();
createApp({ render: () => h('main', { style: 'width:100%;box-sizing:border-box;max-width:960px;margin:48px auto;padding:0 24px' }, [h(Zone)]) })
  .use(router).use(store).mount('#app');
