import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { createStore } from 'vuex';
import { renderToString } from 'vue/server-renderer';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { applyItemExpiration } = await server.ssrLoadModule('/src/core/itemUpkeep.ts');
const { default: ItemInfo } = await server.ssrLoadModule('/src/components/game/ItemInfo.vue');

test('expiration removes exact nested keys only in the matching live runtime and room', () => {
  const room = { id: 5, inventory: [{ key: 'item.1', inventory: [
    { key: 'item.2' }, { key: 'item.3' },
  ] }, { key: 'item.4' }] };
  const state = { world: { id: 7 }, room };
  const payload = { runtime_world_id: 8, room_id: 5, item_keys: ['item.2', 'item.4'] };
  applyItemExpiration(state, payload);
  assert.equal(state.room, room);
  applyItemExpiration(state, { ...payload, runtime_world_id: 7, room_id: 6 });
  assert.equal(state.room, room);
  applyItemExpiration(state, { ...payload, runtime_world_id: 7 });
  assert.equal(state.room.inventory.length, 1);
  assert.deepEqual(state.room.inventory[0].inventory, [{ key: 'item.3' }]);
  assert.equal(room.inventory.length, 2, 'console snapshots stay unchanged');
  assert.equal(room.inventory[0].inventory.length, 2);
  applyItemExpiration(state, { ...payload, runtime_world_id: 7 });
  assert.equal(state.room.inventory.length, 1, 'retries are idempotent');
});

test('corpse inspection uses escaped, static feedback on desktop and mobile', async () => {
  for (const is_mobile of [false, true]) {
    const notice = 'At inspection: expires in about 10 minutes, including anything left inside. <script>';
    const app = createSSRApp(ItemInfo, { item: { key: 'item.1', name: 'a corpse',
      type: 'corpse', inventory: [], upkeep_notice: notice }, player: {}, world: {} });
    app.use(createStore({ state: { game: { is_mobile, player: {}, world: {} } } }));
    app.directive('interactive', {});
    const html = await renderToString(app);
    assert.match(html, /color-text-50 mt-2/);
    assert.match(html, /about 10 minutes/);
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>/);
  }
});
