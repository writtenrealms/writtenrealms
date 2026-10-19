import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { createStore } from 'vuex';
import { renderToString } from 'vue/server-renderer';
import { deathArrival } from './fixtures/deathArrival.mjs';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  plugins: [{
    name: 'test-game-router', enforce: 'pre',
    resolveId(id) {
      if (id.endsWith('/src/router')) return '\0test-game-router';
    },
    load(id) {
      if (id === '\0test-game-router') return 'export default { push() {} }';
    },
  }],
});
after(() => server.close());
const { default: game } = await server.ssrLoadModule('/src/store/modules/game.ts');
const { default: Console } = await server.ssrLoadModule('/src/components/game/console/Console.vue');

const harness = async (is_mobile = false) => {
  let receive;
  const store = createStore({ modules: { game: {
    ...game,
    state: {
      ...structuredClone(game.state), is_mobile, player_config: {},
      player: { id: 3, key: 'player.3' },
      world: { id: 26, instance_ref: 'battlefield-run' },
      map: { 'room.99': { id: 99, key: 'room.99' } },
    },
    mutations: { ...game.mutations, openWS: (_state, callbacks) => { receive = callbacks.onmessage; } },
  } }, mutations: { 'ui/notification_set': () => {} } });
  await store.dispatch('game/openWebSocket');
  return {
    store,
    receive: message => receive({ data: JSON.stringify(message) }),
    render: () => renderToString(createSSRApp(Console).use(store).directive('interactive', {})),
  };
};

test('instance death shows one room, restores world state, and retains room controls', async (t) => {
  t.mock.method(console, 'log', () => {});
  for (const mobile of [false, true]) {
    const { store, receive, render } = await harness(mobile);
    const { death, sync, recovery } = deathArrival();
    receive(death);
    receive(sync);
    receive(recovery);
    // Redelivery must not add a room or replace its interactive history entry.
    receive(death);
    receive(sync);
    const html = await render();
    assert.equal(html.match(/The Underworld/g)?.length, 1);
    const text = html.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ');
    assert.equal(text.match(/A starless black vault/g)?.length, 1);
    assert.equal(html.match(/You have been slain\./g)?.length, 1);
    assert.ok(html.indexOf('You have been slain.') < html.indexOf('The Underworld'));
    assert.ok(html.indexOf('The Underworld') < html.indexOf('You slowly come back'));
    assert.match(html, /<button[^>]*>TOUCH STONE<\/button>/);
    assert.equal(store.state.game.last_viewed_room_message, store.state.game.messages[0]);
    assert.equal(store.state.game.messages.length, 2);
    assert.equal(store.state.game.world.id, 13);
    assert.equal(store.state.game.world.instance_ref, null);
    assert.deepEqual(Object.keys(store.state.game.map).sort(), ['room.10', 'room.7']);
    assert.equal(store.state.game.room.key, 'room.10');
    assert.equal(store.state.game.player.health, 1);
    assert.equal(store.state.game.instance_time_control, null);
    assert.equal(store.state.game.combat.current, null);
  }
});

test('ordinary state refreshes and reconnects still display the room', async (t) => {
  t.mock.method(console, 'log', () => {});
  for (const type of ['cmd.state.sync.success', 'system.connect.success']) {
    const { store, receive, render } = await harness();
    const { sync } = deathArrival();
    delete sync.data.reason;
    sync.type = type;
    receive(sync);
    assert.equal((await render()).match(/The Underworld/g)?.length, 1);
    assert.equal(store.state.game.last_viewed_room_message, store.state.game.messages[0]);
  }
});

test('same-world death remains visible without a full state refresh', async (t) => {
  t.mock.method(console, 'log', () => {});
  const { receive, render } = await harness();
  receive(deathArrival().death);
  const html = await render();
  assert.equal(html.match(/The Underworld/g)?.length, 1);
  assert.match(html, /You have been slain\./);
});
