import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';
import { createRouter, createMemoryHistory } from 'vue-router';
import axios from 'axios';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false, ws: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { default: Account } = await server.ssrLoadModule('/src/views/auth/Account.vue');
const { default: Email } = await server.ssrLoadModule('/src/components/account/EmailSettings.vue');
const { default: Header } = await server.ssrLoadModule('/src/components/Header.vue');
const { platformConfig } = await server.ssrLoadModule('/src/core/platform.ts');

async function context(save = async () => {}) {
  const user = { id: 1, name: 'Alice', email: 'alice@example.com', is_confirmed: true,
    send_newsletter: false, accessibility_mode: false };
  const store = createStore({
    state: { auth: { user } },
    getters: { isAuthenticated: () => true },
    actions: { 'auth/account_save': async ({ state }, payload) => {
      await save(payload);
      Object.assign(state.auth.user, payload);
    } },
  });
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/account', name: 'account', component: {} },
    { path: '/lobby', name: 'lobby', component: {} },
  ] });
  await router.push('/account');
  const app = component => createSSRApp(component).use(store).use(router);
  async function bindings(component) {
    let result;
    await renderToString(app({ setup() { result = component.setup({}, { expose() {} }); return () => null; } }));
    return result;
  }
  return { store, router, app, bindings };
}

test('account drafts stay local until save and only send editable profile fields', async () => {
  const requests = [];
  const ctx = await context(async payload => requests.push(payload));
  const form = await ctx.bindings(Account);
  form.draft.name = 'Onyx';
  form.draft.accessibility_mode = true;
  assert.equal(ctx.store.state.auth.user.name, 'Alice');
  await form.save();
  assert.deepEqual(requests, [{ name: 'Onyx', send_newsletter: false, accessibility_mode: true }]);
  assert.equal(form.saved.value, true);
  assert.deepEqual({ ...form.draft }, form.baseline.value);
  assert.equal(ctx.store.state.auth.user.name, 'Onyx');
});

test('failed profile saves keep edits, surface field errors, and allow retry', async () => {
  let failure = { response: { data: { name: ['This username is taken.'] } } };
  const ctx = await context(async () => { if (failure) throw failure; });
  const form = await ctx.bindings(Account);
  form.draft.name = 'Onyx';
  await form.save();
  assert.equal(form.draft.name, 'Onyx');
  assert.equal(form.dirty.value, true);
  assert.equal(form.saveError.value, 'This username is taken.');
  failure = { response: { data: 'Internal Server Error' } };
  await form.save();
  assert.deepEqual(form.fieldErrors.value, {});
  assert.equal(form.saving.value, false);
  failure = null;
  await form.save();
  assert.equal(form.saved.value, true);
});

test('Account always includes subscriptions and passes regardless of issuance configuration', async () => {
  const ctx = await context();
  for (const config of [null, { main_world_id: 12, building_enabled: false, founders_enabled: false },
    { main_world_id: 12, building_enabled: false, founders_enabled: true }]) {
    platformConfig.value = config;
    const header = await renderToString(ctx.app(Header));
    assert.match(header, /href="\/account"[^>]*class="[^"]*selected[^"]*"[^>]*>Account<\/a>/);
    assert.doesNotMatch(header, />Passes<\/a>/);
    const html = await renderToString(ctx.app(Account));
    assert.match(html, /<h1[^>]*>Account<\/h1>/);
    assert.match(html, /Profile/);
    assert.match(html, /Preferences/);
    assert.equal(html.includes('id="passes"'), true);
    assert.match(html, /Subscriptions &amp; passes/);
    assert.doesNotMatch(html, /founder/i);
    assert.doesNotMatch(html, /single-form|class="panel/);
  }
});

test('email change reports the submitted destination even after the field is edited', async () => {
  const ctx = await context();
  const form = await ctx.bindings(Email);
  let finish;
  const original = axios.post;
  axios.post = async (url, payload) => {
    assert.equal(url, '/auth/email/change/');
    assert.equal(payload.email, 'new@example.com');
    await new Promise(resolve => { finish = resolve; });
  };
  try {
    form.email.value = ' new@example.com ';
    const pending = form.submit();
    form.email.value = 'changed@example.com';
    finish(); await pending;
    assert.equal(form.sentTo.value, 'new@example.com');
    assert.equal(ctx.store.state.auth.user.email, 'alice@example.com');
  } finally { axios.post = original; }
});
