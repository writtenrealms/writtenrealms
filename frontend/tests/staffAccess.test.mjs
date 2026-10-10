import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createStore } from 'vuex';
import { createMemoryHistory, createRouter } from 'vue-router';
import axios from 'axios';

const previousStorage = globalThis.localStorage;
globalThis.localStorage = {
  getItem(key) { return this[key] ?? null; },
  setItem(key, value) { this[key] = String(value); },
  removeItem(key) { delete this[key]; },
};
const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(async () => {
  globalThis.localStorage = previousStorage;
  await server.close();
});
const { default: auth } = await server.ssrLoadModule('/src/store/modules/auth.ts');
const { requireStaff } = await server.ssrLoadModule('/src/core/staffAccess.ts');

function accountStore(isStaff, token = 'existing-login') {
  const notices = [];
  const store = createStore({
    modules: {
      auth: {
        ...auth,
        state: () => ({ token, refreshToken: '', status: '', user: { id: 2, is_staff: isStaff } }),
      },
      ui: { namespaced: true, mutations: { notification_set_error: (_, message) => notices.push(message) } },
    },
    getters: { isAuthenticated: state => Boolean(state.auth.token) },
  });
  return { store, notices };
}

function mockAccount(t, get) {
  const original = axios.get;
  axios.get = get;
  t.after(() => { axios.get = original; });
}

test('a newly promoted staff user reaches the actual guarded route despite stale browser permissions', async t => {
  const { store, notices } = accountStore(false);
  mockAccount(t, async url => {
    assert.equal(url, '/user/');
    return { data: { id: 2, email: 'staff@example.com', name: null, is_staff: true } };
  });
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/staff', component: {}, beforeEnter: to => requireStaff(store, to) },
      { path: '/lobby', component: {} },
    ],
  });
  await router.push('/staff');
  assert.equal(router.currentRoute.value.path, '/staff');
  assert.equal(store.state.auth.user.is_staff, true);
  assert.equal(JSON.parse(localStorage.getItem('user')).is_staff, true);
  assert.deepEqual(notices, []);
});

test('revoked staff permission overrides a cached staff grant', async t => {
  const { store, notices } = accountStore(true);
  mockAccount(t, async () => ({ data: { id: 2, is_staff: false } }));
  assert.equal(await requireStaff(store, { fullPath: '/staff' }), '/lobby');
  assert.equal(store.state.auth.user.is_staff, false);
  assert.deepEqual(notices, ['Access denied.']);
});

test('startup and staff navigation share one request, but later navigation rechecks permissions', async t => {
  const { store } = accountStore(false);
  let finish;
  let calls = 0;
  mockAccount(t, () => {
    calls++;
    return new Promise(resolve => { finish = resolve; });
  });
  const startup = store.dispatch('auth/refresh_user');
  const navigation = requireStaff(store, { fullPath: '/staff' });
  assert.equal(calls, 1);
  finish({ data: { id: 2, is_staff: true } });
  await startup;
  assert.equal(await navigation, true);
  const later = requireStaff(store, { fullPath: '/staff' });
  assert.equal(calls, 2);
  finish({ data: { id: 2, is_staff: false } });
  assert.equal(await later, '/lobby');
});

test('a failed permission refresh does not admit cached staff and can be retried', async t => {
  const { store, notices } = accountStore(true);
  let calls = 0;
  mockAccount(t, async () => {
    if (++calls === 1) throw new Error('offline');
    return { data: { id: 2, is_staff: true } };
  });
  assert.equal(await requireStaff(store, { fullPath: '/staff' }), '/lobby');
  assert.deepEqual(notices, ['Could not verify staff access. Please try again.']);
  assert.equal(await requireStaff(store, { fullPath: '/staff' }), true);
});

test('anonymous requests go to login without querying the account API', async t => {
  const { store } = accountStore(true, '');
  mockAccount(t, () => assert.fail('Anonymous visitor must not fetch account data'));
  assert.equal(await store.dispatch('auth/refresh_user'), null);
  assert.deepEqual(await requireStaff(store, { fullPath: '/staff?section=jobs' }), {
    path: '/login', query: { redirect: '/staff?section=jobs' },
  });
});

test('logout clears the cached account and discards an outstanding account response', async t => {
  const { store } = accountStore(true);
  let finish;
  mockAccount(t, () => new Promise(resolve => { finish = resolve; }));
  const pending = store.dispatch('auth/refresh_user');
  store.commit('auth/auth_clear');
  finish({ data: { id: 2, is_staff: true } });
  assert.equal(await pending, null);
  assert.deepEqual(store.state.auth.user, {});
  assert.equal(localStorage.getItem('user'), null);
});

test('an outstanding account response cannot overwrite a different login', async t => {
  const { store } = accountStore(true);
  let finish;
  mockAccount(t, () => new Promise(resolve => { finish = resolve; }));
  const pending = store.dispatch('auth/refresh_user');
  store.commit('auth/auth_set_tokens', { access: 'different-login' });
  store.commit('auth/user_set', { id: 3, is_staff: false });
  finish({ data: { id: 2, is_staff: true } });
  assert.equal(await pending, null);
  assert.equal(store.state.auth.user.id, 3);
  assert.equal(store.state.auth.user.is_staff, false);
});
