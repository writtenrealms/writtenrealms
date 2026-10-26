import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';
import { rootWorld, worldInstance } from './fixtures/worldAdmin.mjs';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { default: AdminInstance } = await server.ssrLoadModule('/src/views/builder/world/AdminInstance.vue');
async function render(instance) {
  const requests = [];
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/build/worlds/:world_id/admin/:instance_id', component: AdminInstance },
    { path: '/build/worlds/:world_id/players/:player_id', name: 'builder_world_player_details', component: {} },
  ] });
  await router.push('/build/worlds/12/admin/13');
  const store = createStore({
    state: { builder: { world: rootWorld, worlds: { admin: { admin_instance: instance } } } },
    actions: { 'builder/worlds/admin/world_admin_instance_fetch': (_, payload) => requests.push(payload) },
  });
  const html = await renderToString(createSSRApp(AdminInstance).use(router).use(store));
  assert.deepEqual(requests, [{ world_id: '12', instance_id: '13', preserveCurrent: false }]);
  return html;
}

test('runtime dashboard retains metrics, details, player navigation and escaped state values', async () => {
  const instance = worldInstance();
  instance.world_state = { markup: '<script>unsafe</script>', flag: false, count: 0, empty: null, nested: { hello: 'world' } };
  const html = await render(instance);
  for (const label of ['Phalanx world #13', 'Mobs loaded', 'Items on ground', 'Items total', 'Players logged in', 'Last spawn-plan run', 'Assignments', 'Runtime timeline', 'World details', 'Item distribution']) assert.ok(html.includes(label), label);
  assert.match(html, /href="\/build\/worlds\/12\/players\/1"/);
  assert.match(html, /The agora/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  for (const value of ['false', 'null', '&quot;hello&quot;:&quot;world&quot;']) assert.ok(html.includes(value), value);
  assert.doesNotMatch(html, /RESET WORLD|>RECOVER<|NaN|undefined/);
});

test('lifecycle controls and status remain explicit for stopped, transitional and failed worlds', async () => {
  for (const [state, tag, reset, recover] of [
    ['running', 'tag-green', false, false], ['starting', 'tag-secondary', false, false],
    ['stopped', '', true, false], ['failed', 'tag-red', false, true],
  ]) {
    const instance = worldInstance();
    instance.lifecycle_details.current = state;
    instance.recovery_actions.recover_to_stopped = recover;
    const html = await render(instance);
    const badge = html.match(new RegExp(`<span class="([^"]*)"[^>]*>${state}</span>`));
    assert.ok(badge, `Missing ${state} status`);
    assert.ok(badge[1].split(" ").includes(tag || "tag"));
    assert.equal(html.includes('RESET WORLD'), reset);
    assert.equal(html.includes('>RECOVER</button>'), recover);
  }
});

test('empty worlds and loading retain readable states', async () => {
  const instance = worldInstance();
  instance.active_players = [];
  instance.world_state = {};
  instance.counts.players_logged_in = 0;
  const html = await render(instance);
  assert.match(html, /No players are currently logged into this world/);
  assert.match(html, /No world state is currently set/);
  assert.match(await render(null), /role="status"[^>]*> Loading world data/);
});
