import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import axios from 'axios';
import { createServer } from 'vite';
import { createRenderer, createSSRApp, nextTick, ssrContextKey } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
  plugins: [{
    name: 'notification-test-router', enforce: 'pre',
    resolveId(id) { if (id.endsWith('/src/router')) return '\0notification-router'; },
    load(id) { if (id === '\0notification-router') return 'export default {}'; },
  }],
});
after(() => server.close());
const { default: Notification } = await server.ssrLoadModule('/src/components/ui/Notification.vue');
const { default: ui } = await server.ssrLoadModule('/src/store/modules/ui/index.ts');
const { default: game } = await server.ssrLoadModule('/src/store/modules/game.ts');
const makeStore = () => createStore({ modules: { ui: { ...ui, state: { ...ui.state } } } });

// Mount the actual setup lifecycle with a null render so expiry tests need no DOM.
const renderer = createRenderer({
  createComment: () => ({}), insert() {}, remove() {},
  parentNode: () => null, nextSibling: () => null,
});
function mountLifecycle(store) {
  let bindings;
  const app = renderer.createApp({ setup() {
    bindings = Notification.setup({}, { expose() {} });
    return () => null;
  } }).use(store).provide(ssrContextKey, { modules: new Set() });
  app.mount({});
  return { app, bindings };
}

test('toast states expose accessible messages and a native dismiss button', async () => {
  for (const [type, icon, role] of [
    ['loading', 'loader-circle', 'status'],
    ['success', 'check', 'status'],
    ['error', 'circle-alert', 'alert'],
  ]) {
    const store = makeStore();
    store.commit('ui/notification_set', { text: 'Test message', type, expires: false });
    const html = await renderToString(createSSRApp(Notification).use(store));
    assert.match(html, new RegExp(`lucide-${icon}`));
    assert.match(html, new RegExp(`role="${role}" aria-atomic="true"`));
    assert.match(html, /<button[^>]*type="button"[^>]*aria-label="Dismiss notification"/);
    assert.match(html, /Test message/);
  }
});

test('detailed errors retain line breaks and escape server text', async () => {
  const store = makeStore();
  store.commit('ui/notification_set_error', 'Could not apply manifest.\n<script>bad()</script>');
  const html = await renderToString(createSSRApp(Notification).use(store));
  assert.ok([...html.matchAll(/class="([^"]+)"/g)].some(([, classes]) =>
    ['ui-notification', 'error', 'multiline'].every(name => classes.split(' ').includes(name))));
  assert.match(html, /Could not apply manifest\.\n&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('replacing a toast resets expiry and gives errors their full display time', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const store = makeStore();
  store.commit('ui/notification_set', 'Saved.');
  const { app } = mountLifecycle(store);
  try {
    t.mock.timers.tick(2500);
    store.commit('ui/notification_set_error', 'Could not save.');
    await nextTick();
    t.mock.timers.tick(4999);
    assert.equal(store.state.ui.notification, 'Could not save.');
    t.mock.timers.tick(1);
    assert.equal(store.state.ui.notification, '');
  } finally { app.unmount(); }
});

test('loading persists, dismissal clears it, and unmount cancels stale expiry', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const store = makeStore();
  store.commit('ui/notification_set', { text: 'Entering world...', type: 'loading', expires: false });
  const { app, bindings } = mountLifecycle(store);
  t.mock.timers.tick(60000);
  assert.equal(store.state.ui.notification, 'Entering world...');
  bindings.closeNotification();
  assert.equal(store.state.ui.notification, '');
  store.commit('ui/notification_set', 'Saved.');
  await nextTick();
  t.mock.timers.tick(2500);
  app.unmount();
  store.commit('ui/notification_set', 'Saved again.');
  const next = mountLifecycle(store);
  try {
    t.mock.timers.tick(2999);
    assert.equal(store.state.ui.notification, 'Saved again.');
    t.mock.timers.tick(1);
    assert.equal(store.state.ui.notification, '');
  } finally { next.app.unmount(); }
});

test('both world-entry paths request a persistent loading toast', async t => {
  t.mock.method(axios, 'post', async () => ({ data: { world: {}, player_config: {} } }));
  for (const action of ['request_enter_world', 'world_enter']) {
    const commits = [];
    await game.actions[action]({
      commit: (...args) => commits.push(args), dispatch: async () => {},
    }, { player_id: 3, world_id: 12 });
    assert.deepEqual(commits.find(([name]) => name === 'ui/notification_set'), [
      'ui/notification_set', { text: 'Entering world...', type: 'loading', expires: false }, { root: true },
    ]);
  }
});
