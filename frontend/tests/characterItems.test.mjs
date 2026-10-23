import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import axios from 'axios';
import { createServer } from 'vite';
import { createRenderer, nextTick, ssrContextKey } from 'vue';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';
import { characterItemsPage } from './fixtures/characterItems.mjs';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { default: CharacterPage } = await server.ssrLoadModule('/src/views/lobby/CharacterPage.vue');
const renderer = createRenderer({
  createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null,
});
async function mountPage(t) {
  const payload = characterItemsPage();
  t.mock.method(axios, 'get', async () => ({ data: payload }));
  const notifications = [];
  const store = createStore({
    state: { auth: { user: { is_temporary: false } } },
    mutations: {
      'ui/notification_set': (_, text) => notifications.push(['success', text]),
      'ui/notification_set_error': (_, text) => notifications.push(['error', text]),
    },
  });
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/characters/:player_id', component: {} },
  ] });
  await router.push('/characters/4');
  let bindings;
  const app = renderer.createApp({ setup() {
    bindings = CharacterPage.setup({}, { expose() {} });
    return () => null;
  } }).use(store).use(router).provide(ssrContextKey, { modules: new Set() });
  app.mount({});
  t.after(() => app.unmount());
  await nextTick();
  return { bindings, payload, notifications };
}

test('salvage eligibility excludes quest items, ordinary items, and filled bags', async t => {
  const { bindings, payload } = await mountPage(t);
  assert.deepEqual(payload.character.inventory.map(item => !!bindings.canSalvage(item)),
    [true, true, false, false, false, true]);
});

test('salvage posts the exact item once, disables actions while pending, and refreshes balances', async t => {
  const { bindings, payload, notifications } = await mountPage(t);
  const selected = payload.character.inventory[0];
  const updated = structuredClone(payload);
  updated.character.inventory.shift();
  updated.character.materials.bronze = 4;
  let resolve;
  const post = t.mock.method(axios, 'post', () => new Promise(done => { resolve = done; }));
  const pending = bindings.act('salvage', selected);
  assert.equal(bindings.canAct.value, false);
  await bindings.act('salvage', selected);
  assert.equal(post.mock.callCount(), 1);
  assert.deepEqual(post.mock.calls[0].arguments, ['/lobby/characters/4/items/',
    { action: 'salvage', item: 'item.2', container: undefined }]);
  resolve({ data: updated });
  await pending;
  assert.equal(bindings.canAct.value, true);
  assert.equal(bindings.inventory.value.some(item => item.key === selected.key), false);
  assert.equal(bindings.materialEntries.value[0].amount, 4);
  assert.deepEqual(notifications, [['success', 'Salvaged a Persian war spear.']]);
});

test('failed salvage preserves inventory, reports the error, and releases pending state', async t => {
  const { bindings, payload, notifications } = await mountPage(t);
  t.mock.method(axios, 'post', async () => { throw { response: { data: { detail: 'Fen is in the game. Manage items there.' } } }; });
  await bindings.act('salvage', payload.character.inventory[0]);
  assert.equal(bindings.inventory.value.length, 6);
  assert.equal(bindings.materialEntries.value[0].amount, 1);
  assert.equal(bindings.busy.value, false);
  assert.deepEqual(notifications, [['error', 'Fen is in the game. Manage items there.']]);
});

test('online characters and public viewers cannot submit item changes', async t => {
  const { bindings, payload } = await mountPage(t);
  const post = t.mock.method(axios, 'post', () => { throw new Error('Must not post'); });
  bindings.data.value = { ...payload, can_manage_items: false, in_game: true };
  await bindings.act('salvage', payload.character.inventory[0]);
  bindings.data.value = { ...payload, view: 'public' };
  await bindings.act('salvage', payload.character.inventory[0]);
  assert.equal(post.mock.callCount(), 0);
});
