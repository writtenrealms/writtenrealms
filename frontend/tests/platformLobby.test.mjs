import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';
import axios from 'axios';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { platformConfig, loadPlatformConfig, siteEntryDestination } =
  await server.ssrLoadModule('/src/core/platform.ts');
const { default: Header } = await server.ssrLoadModule('/src/components/Header.vue');

test('all site entry points promote the configured world for guests and members', () => {
  for (const name of ['home', 'homedirect', 'lobby']) {
    for (const id of [1, 12]) {
      for (const authenticated of [false, true]) {
        assert.deepEqual(siteEntryDestination({ main_world_id: id }, {
          name, query: { create: '1' }, hash: '#characters', fullPath: '/lobby?create=1#characters',
        }, authenticated), {
          name: 'lobby_world_details', params: { world_id: id },
          query: { create: '1' }, hash: '#characters',
        });
      }
    }
  }
});

test('multi-world mode preserves homepages and the authenticated directory', () => {
  const config = { main_world_id: null };
  for (const name of ['home', 'homedirect']) {
    assert.equal(siteEntryDestination(config, { name }, false), null);
  }
  assert.equal(siteEntryDestination(config, { name: 'lobby' }, true), null);
  assert.deepEqual(siteEntryDestination(config, { name: 'lobby', fullPath: '/lobby?create=1' }, false), {
    name: 'login', query: { redirect: '/lobby?create=1' },
  });
});

test('entry and header share one request, retained across navigation', async () => {
  const original = axios.get;
  platformConfig.value = null;
  let finish;
  let count = 0;
  axios.get = url => {
    assert.equal(url, '/lobby/config/');
    count++;
    return new Promise(resolve => { finish = resolve; });
  };
  try {
    const first = loadPlatformConfig();
    const second = loadPlatformConfig();
    assert.equal(first, second);
    finish({ data: { main_world_id: 12 } });
    await Promise.all([first, second]);
    assert.deepEqual(await loadPlatformConfig(), { main_world_id: 12 });
    assert.equal(count, 1);
  } finally { axios.get = original; }
});

test('failed or malformed config can be retried without guessing a destination', async () => {
  const original = axios.get;
  try {
    for (const data of [undefined, {}, { main_world_id: 0 }, { main_world_id: '12' }, { main_world_id: -1 }]) {
      platformConfig.value = null;
      axios.get = async () => {
        if (data === undefined) throw new Error('offline');
        return { data };
      };
      await assert.rejects(loadPlatformConfig());
      assert.equal(platformConfig.value, null);
      axios.get = async () => ({ data: { main_world_id: null } });
      assert.deepEqual(await loadPlatformConfig(), { main_world_id: null });
    }
  } finally { axios.get = original; }
});

test('header offers Lobby and Build in single-world mode, Worlds in multi-world mode', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/home', name: 'home', component: {} },
      { path: '/lobby', name: 'lobby', component: {} },
      { path: '/lobby/:section', name: 'lobby_section', component: {} },
      { path: '/worlds/:world_id', name: 'lobby_world_details', component: {} },
    ],
  });
  await router.push('/worlds/12');
  const store = createStore({
    state: { auth: { user: {} } }, getters: { isAuthenticated: () => true },
  });
  const render = () => renderToString(createSSRApp(Header).use(router).use(store));
  platformConfig.value = { main_world_id: 12 };
  const single = await render();
  assert.match(single, /href="\/lobby"[^>]*class="selected"[^>]*>Lobby<\/a>/);
  assert.match(single, /href="\/lobby\/building"[^>]*>Build<\/a>/);
  assert.doesNotMatch(single, />Worlds<\/a>/);
  platformConfig.value = { main_world_id: null };
  assert.match(await render(), />Worlds<\/a>/);
});
