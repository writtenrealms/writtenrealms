import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createStore } from 'vuex';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
  plugins: [{
    name: 'game-exit-router', enforce: 'pre',
    resolveId(id) { if (id.endsWith('/src/router')) return '\0game-exit-router'; },
    load(id) {
      if (id === '\0game-exit-router') return `export default {
        visits: [], currentRoute: { value: { name: 'game', params: {} } },
        push(route) {
          this.visits.push(route);
          this.currentRoute.value = { ...route, params: Object.fromEntries(
            Object.entries(route.params || {}).map(([key, value]) => [key, String(value)])) };
        },
      };`;
    },
  }],
});
after(() => server.close());
const { default: game } = await server.ssrLoadModule('/src/store/modules/game.ts');
const { default: router } = await server.ssrLoadModule('/src/router');
const { default: forge } = await server.ssrLoadModule('/src/store/modules/forge.ts');

async function harness(t, { instance = true, temporary = false, transfer = false } = {}) {
  t.mock.method(console, 'log', () => {});
  router.visits.length = 0;
  router.currentRoute.value = { name: 'game', params: {} };
  let callbacks;
  let closed = 0;
  let loggedOut = 0;
  const store = createStore({ modules: {
    auth: { namespaced: true, state: { user: { is_temporary: temporary } },
      actions: { logout: () => { loggedOut += 1; } } },
    forge,
    game: {
      ...game,
      state: { ...structuredClone(game.state), player: {},
        world: { id: 99, context_id: instance ? 17 : 12, instance_of_id: instance ? 12 : null },
        transfer_to: transfer ? { transfer_to_world_id: 20 } : {},
      },
      mutations: { ...game.mutations,
        openWS: (_state, handlers) => { callbacks = handlers; },
        closeWs: () => { closed += 1; },
      },
    },
  } });
  await store.dispatch('game/openWebSocket');
  return {
    store,
    disconnect: () => callbacks.onmessage({ data: JSON.stringify({ type: 'system.disconnect.success', data: {} }) }),
    completion: (exit_to = 12) => store.dispatch('forge/job_complete', {
      job: 'exit_world', status: 'success', job_data: { world_id: 99, exit_to },
    }),
    closed: () => closed,
    loggedOut: () => loggedOut,
  };
}

test('quitting inside an instance immediately routes to its base-world lobby', async t => {
  const { disconnect, store, closed } = await harness(t);
  await disconnect();
  assert.deepEqual(router.visits, [{ name: 'lobby_world_details', params: { world_id: 12 } }]);
  assert.equal(store.state.game.world, null);
  assert.equal(closed(), 1);
});

for (const first of ['disconnect', 'completion']) {
  test(`exit notifications route only once when ${first} arrives first`, async t => {
    const flow = await harness(t);
    await flow[first]();
    await flow[first === 'disconnect' ? 'completion' : 'disconnect']();
    assert.deepEqual(router.visits, [{ name: 'lobby_world_details', params: { world_id: 12 } }]);
  });
}

test('completion without a destination falls back to the instance base world', async t => {
  const { completion } = await harness(t);
  await completion(null);
  assert.equal(router.visits[0].params.world_id, 12);
});

test('ordinary-world exits continue to use that world lobby', async t => {
  const { disconnect } = await harness(t, { instance: false });
  await disconnect();
  assert.equal(router.visits[0].params.world_id, 12);
});

test('explicit server destinations are respected', async t => {
  const { completion } = await harness(t);
  await completion(25);
  assert.equal(router.visits[0].params.world_id, 25);
});

test('temporary characters still log out to the home page', async t => {
  const { disconnect, loggedOut } = await harness(t, { temporary: true });
  await disconnect();
  assert.deepEqual(router.visits, [{ name: 'home' }]);
  assert.equal(loggedOut(), 1);
});

test('world-transfer completion does not redirect to a lobby or clear the game', async t => {
  const { completion, store, closed } = await harness(t, { transfer: true });
  await completion();
  assert.deepEqual(router.visits, []);
  assert.equal(store.state.game.world.id, 99);
  assert.equal(closed(), 0);
});
