import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';
import { renderToString } from 'vue/server-renderer';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { characterCreationNotice } = await server.ssrLoadModule('/src/core/characterCreation.ts');
const { default: lobby } = await server.ssrLoadModule('/src/store/modules/lobby.ts');
const { default: CreateChar } = await server.ssrLoadModule('/src/components/lobby/CreateChar.vue');

function makeWorld(limit = { maximum: 1, used: 1 }) {
  return {
    can_create_chars: true, character_creation_limit: limit, is_classless: true,
    can_select_gender: true, core_factions: [], player_creation: {},
  };
}

test('limit messages handle one, several, unlimited, and closed creation', () => {
  assert.equal(characterCreationNotice(makeWorld()), 'This world allows 1 character per account. You have reached the limit.');
  assert.match(characterCreationNotice(makeWorld({ maximum: 2, used: 3 })), /2 characters per account/);
  assert.equal(characterCreationNotice(makeWorld({ maximum: 2, used: 1 })), '');
  assert.equal(characterCreationNotice(makeWorld(null)), '');
  assert.match(characterCreationNotice({ ...makeWorld(null), can_create_chars: false }), /disabled/);
});

test('creating a normal character immediately consumes a slot; Builder creation does not', () => {
  const store = createStore({ modules: { lobby: { ...lobby, state: { world: makeWorld({ maximum: 1, used: 0 }), chars: [] } } } });
  store.commit('lobby/char_create', { id: 1, is_builder: true });
  assert.equal(characterCreationNotice(store.state.lobby.world), '');
  store.commit('lobby/char_create', { id: 2, is_builder: false });
  assert.match(characterCreationNotice(store.state.lobby.world), /reached the limit/);
});

test('an already-open creation form becomes disabled at the cap and remains available below it', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/worlds/:world_id', component: {} }] });
  await router.push('/worlds/12');
  for (const used of [0, 1]) {
    const store = createStore({ state: { lobby: { world: makeWorld({ maximum: 1, used }) } } });
    const html = await renderToString(createSSRApp(CreateChar).use(store).use(router));
    if (used) {
      assert.match(html, /You have reached the limit/);
      assert.doesNotMatch(html, /Builder characters do not count/);
      assert.match(html, /<button class="btn-large" disabled/);
    } else {
      assert.doesNotMatch(html, /<button class="btn-large" disabled/);
      assert.doesNotMatch(html, /reached the limit/);
    }
  }
});
