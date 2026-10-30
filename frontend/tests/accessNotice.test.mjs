import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import axios from 'axios';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';

const storage = new Map();
globalThis.localStorage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
};
const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
  plugins: [{
    name: 'test-auth-context', enforce: 'pre',
    resolveId(id) {
      if (id.endsWith('/src/router')) return '\0auth-router';
      if (id.endsWith('/src/store')) return '\0auth-store';
    },
    load(id) {
      if (id === '\0auth-router') return 'export default globalThis.authTestRouter';
      if (id === '\0auth-store') return 'export default globalThis.authTestStore';
    },
  }],
});
after(() => server.close());
const { default: auth } = await server.ssrLoadModule('/src/store/modules/auth.ts');
const { default: ui } = await server.ssrLoadModule('/src/store/modules/ui/index.ts');
const store = globalThis.authTestStore = createStore({ modules: { auth, ui } });
const router = globalThis.authTestRouter = createRouter({
  history: createMemoryHistory(),
  routes: [
    ...['signup', 'login', 'lobby', 'game', 'account'].map(name => ({ path: `/${name}`, name, component: {} })),
    { path: '/login-link/:token', name: 'loginlink', component: {} },
  ],
});
const { default: setup } = await server.ssrLoadModule('/src/core/axiosInterceptors.ts');
setup();
const { default: Signup } = await server.ssrLoadModule('/src/views/auth/SignUp.vue');
const { default: Login } = await server.ssrLoadModule('/src/views/auth/Login.vue');
const { default: LoginLink } = await server.ssrLoadModule('/src/views/auth/LoginLink.vue');
const { accessNotice } = await server.ssrLoadModule('/src/core/accessNotice.ts');

const invite = { status: 403, data: { code: 'invite_only', detail:
  'This site is currently invite-only. Please sign in with an invited email address, or contact the administrator to request an invitation.' } };
const closed = { status: 403, data: { code: 'site_closed', detail: 'This site is currently closed. Please check back later.' } };
let failure;
axios.defaults.adapter = async config => {
  if (failure instanceof Error) throw failure;
  if (failure) throw { response: failure };
  return { config, status: 201, statusText: 'Created', headers: {}, data: {
    access: 'test-access', refresh: 'test-refresh', user: { id: 1, name: 'Alice', email: 'alice@example.com' },
  } };
};
async function form(component, name) {
  await router.push(`/${name}?redirect=/worlds/12?create=1`);
  store.commit('ui/notification_clear');
  let bindings;
  await renderToString(createSSRApp({ setup() {
    bindings = component.setup({}, { expose() {} });
    return () => null;
  } }).use(store).use(router));
  bindings.email.value = 'visitor@example.com';
  return bindings;
}

test('signup and login keep admission notices inline, preserve input, and allow a successful retry', async () => {
  for (const [component, name] of [[Signup, 'signup'], [Login, 'login']]) {
    for (const response of [invite, closed]) {
      const bindings = await form(component, name);
      failure = response;
      await bindings[name]();
      assert.equal(bindings.admissionMessage.value, response.data.detail);
      assert.equal(bindings.email.value, 'visitor@example.com');
      assert.equal(bindings.sent.value, false);
      assert.equal(store.state.ui.notification, '');
      assert.equal(router.currentRoute.value.name, name);
      failure = null;
      await bindings[name]();
      assert.equal(bindings.admissionMessage.value, '');
      assert.equal(bindings.sent.value, true);
    }
  }
});

test('Google admission denial stays on either auth form without a generic banner', async () => {
  for (const [component, name] of [[Signup, 'signup'], [Login, 'login']]) {
    const bindings = await form(component, name);
    failure = invite;
    await bindings.googleLoginCallback({ credential: 'test-credential' });
    assert.equal(bindings.admissionMessage.value, invite.data.detail);
    assert.equal(store.state.ui.notification, '');
    assert.equal(router.currentRoute.value.name, name);
  }
});

test('other permission errors still show server details or a safe fallback', async () => {
  await router.push('/signup');
  for (const [data, expected] of [
    [{ detail: 'Building is currently disabled.' }, 'Building is currently disabled.'],
    [{}, 'You are not authorized to perform this action'],
    [{ detail: { unexpected: 'object' } }, 'You are not authorized to perform this action'],
    [{ detail: '  ' }, 'You are not authorized to perform this action'],
  ]) {
    failure = { status: 403, data };
    await assert.rejects(axios.post('/test/'));
    assert.equal(store.state.ui.notification, expected);
    assert.equal(accessNotice({ response: failure }), '');
  }
  assert.equal(accessNotice(new Error('offline')), '');
  await router.push('/lobby');
  failure = invite;
  await assert.rejects(axios.post('/test/'));
  assert.equal(store.state.ui.notification, invite.data.detail);
});

test('login links distinguish service, network, rate-limit, admission, and actual token failures', async () => {
  storage.delete('postLoginRedirect');
  await router.push('/login-link/test-token');
  let bindings;
  await renderToString(createSSRApp({ setup() {
    bindings = LoginLink.setup({}, { expose() {} });
    return () => null;
  } }).use(store).use(router));
  for (const [response, expected, retryable] of [
    [{ status: 500, data: 'Internal Server Error' }, /temporarily unavailable/, true],
    [new Error('Network Error'), /Unable to reach/, true],
    [{ status: 429, data: {} }, /Too many/, true],
    [invite, /invite-only/, false],
    [{ status: 400, data: { token: ['Login link has already been used.'] } }, /already been used/, false],
    [{ status: 400, data: { token: ['Invalid or expired login link.'] } }, /Invalid or expired/, false],
  ]) {
    store.commit('ui/notification_clear');
    failure = response;
    await bindings.confirmLogin();
    assert.equal(bindings.status.value, 'error');
    assert.match(bindings.errorMessage.value, expected);
    assert.equal(bindings.retryable.value, retryable);
    assert.equal(store.state.ui.notification, '');
  }
  failure = null;
  await bindings.confirmLogin();
  assert.equal(bindings.status.value, 'success');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(router.currentRoute.value.name, 'lobby');
});

test('network failures outside login-link pages retain the original error', async () => {
  await router.push('/signup');
  failure = new Error('Network Error');
  await assert.rejects(axios.post('/test/'), error => error === failure);
});

test('account validation stays inline while unauthorized sessions are still cleared', async () => {
  await router.push('/account');
  for (const status of [400, 403]) {
    store.commit('ui/notification_clear');
    failure = { status, data: { detail: 'Account action denied.' } };
    await assert.rejects(axios.post('/test/'));
    assert.equal(store.state.ui.notification, '');
  }
  store.commit('auth/auth_set_tokens', { access: 'expired' });
  failure = { status: 401, data: {} };
  await assert.rejects(axios.get('/user/'));
  assert.equal(store.state.auth.token, '');
});
