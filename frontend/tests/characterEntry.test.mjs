import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp, defineComponent, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { useCharEntry } = await server.ssrLoadModule('/src/composables/useCharEntry.ts');

async function characterPageEntry(data) {
  const entered = [];
  const store = createStore({
    state: { auth: { user: { id: 1, is_temporary: false } } },
    actions: { 'game/request_enter_world': (_context, payload) => entered.push(payload) },
  });
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/characters/:player_id', name: 'character_details', component: {} },
      { path: '/world/transfer/:player_id', name: 'lobby_world_transfer', component: {} },
    ],
  });
  await router.push(`/characters/${data.id}`);
  let entry;
  const Probe = defineComponent({
    setup() {
      entry = useCharEntry();
      return () => h('button', entry.needsTransfer(data) ? 'TRANSFER' : 'PLAY AS');
    },
  });
  const html = await renderToString(createSSRApp(Probe).use(store).use(router));
  return { html, entered, router, entry };
}

test('completed character pages transfer instead of requesting game entry', async () => {
  // The lobby-page metadata carries entry eligibility; the game Actor does not.
  const data = { id: 7, can_transfer: true, world: { id: 12 }, character: { id: 7 } };
  const { html, entered, router, entry } = await characterPageEntry(data);
  assert.match(html, /TRANSFER/);
  const transferred = new Promise(resolve => router.afterEach(resolve));
  entry.playChar(data, data.world.id);
  await transferred;
  assert.equal(router.currentRoute.value.name, 'lobby_world_transfer');
  assert.equal(router.currentRoute.value.params.player_id, '7');
  assert.deepEqual(entered, []);
});

test('ordinary character pages enter their base world', async () => {
  const data = { id: 7, can_transfer: false, world: { id: 12 }, character: { id: 7 } };
  const { html, entered, router, entry } = await characterPageEntry(data);
  assert.match(html, /PLAY AS/);
  entry.playChar(data, data.world.id);
  assert.deepEqual(entered, [{ player_id: 7, world_id: 12 }]);
  assert.equal(router.currentRoute.value.name, 'character_details');
});
