import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import axios from 'axios';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createRouter, createMemoryHistory } from 'vue-router';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false, ws: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { default: Subscriptions } = await server.ssrLoadModule('/src/components/account/Subscriptions.vue');
const { platformConfig } = await server.ssrLoadModule('/src/core/platform.ts');
const direct = { id: 1, world_id: 12, world_name: 'Phalanx', kind: 'direct', claimed_at: null,
  can_claim: true, can_transfer: true, can_invite: false, transfer_email: null };

async function render(passes, config, error = null) {
  platformConfig.value = config;
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/account', name: 'account', component: {} },
    { path: '/lobby', name: 'lobby', component: {} },
    { path: '/worlds/:world_id', name: 'lobby_world_details', component: {} },
  ] });
  await router.push('/account');
  const original = axios.get;
  let requests = 0;
  axios.get = async url => {
    assert.equal(url, '/founders/');
    requests++;
    if (error) throw error;
    return { data: { passes } };
  };
  try {
    // Render the actual section after its load, without relying on a DOM mount
    // or changing production component props just for fixtures.
    const Loaded = { ...Subscriptions, async setup(props, context) {
      const bindings = Subscriptions.setup(props, context);
      await bindings.load();
      return bindings;
    } };
    const html = await renderToString(createSSRApp(Loaded).use(router));
    assert.equal(requests, 1);
    return html;
  } finally { axios.get = original; }
}

test('empty accounts see a generic section without advertising private pass types', async () => {
  for (const enabled of [false, true]) {
    const html = await render([], { main_world_id: 12, founders_enabled: enabled });
    assert.match(html, /Subscriptions &amp; passes/);
    assert.match(html, /You don’t have any subscriptions or passes/);
    assert.match(html, /Visit the lobby/);
    assert.doesNotMatch(html, /founder|invitation|Claim on this account|Subscribe<|Manage payment/i);
  }
});

test('multi-world empty accounts can browse worlds without suggesting a purchase', async () => {
  const html = await render([], { main_world_id: null, founders_enabled: false });
  assert.match(html, /href="\/lobby"/);
  assert.match(html, /Browse worlds/);
  assert.doesNotMatch(html, /founder/i);
});

test('only holders see the Founder’s Pass label and their available controls', async () => {
  const html = await render([direct], { main_world_id: 12, founders_enabled: true });
  assert.match(html, /<h3[^>]*>Phalanx Founder Pass<\/h3>/);
  assert.match(html, /class="pass-ticket"/);
  assert.doesNotMatch(html, /Ready to claim|No date has been set|Automatically claimed at the end of beta/);
  assert.match(html, /class="account-note"[^>]*>Claim this pass/);
  assert.match(html, /Claim on this account/);
  assert.match(html, /Transfer to another account/);
  assert.doesNotMatch(html, /You don’t have any subscriptions/);
});

test('an existing pass remains visible when issuance is disabled', async () => {
  const html = await render([{ ...direct, claimed_at: '2026-10-30', can_claim: false, can_transfer: false }],
    { main_world_id: 12, founders_enabled: false });
  assert.match(html, /Phalanx Founder Pass/);
  assert.match(html, /Claimed <time datetime="2026-10-30"[^>]*>/);
  assert.doesNotMatch(html, /Claim on this account|Transfer to another account|Your \+1’s email/);
});

test('claimed passes show their local claim date below the perforation without a duplicate badge', async () => {
  const previousTZ = process.env.TZ;
  process.env.TZ = 'America/Denver';
  try {
    for (const kind of ['direct', 'gift']) {
      const html = await render([{ ...direct, kind, claimed_at: '2026-10-31T01:30:00Z', can_claim: false, can_transfer: false }], null);
      assert.match(html, /class="pass-ticket-stub"[\s\S]*Claimed <time datetime="2026-10-31T01:30:00Z"[^>]*>2026\/10\/30<\/time>/);
      assert.doesNotMatch(html, /class="tag|Claimed on your account|change your email/);
    }
  } finally {
    if (previousTZ === undefined) delete process.env.TZ;
    else process.env.TZ = previousTZ;
  }
});

test('load failures show retry feedback without inventing an empty account or exposing a pass type', async () => {
  const html = await render([], null, new Error('offline'));
  assert.match(html, /role="alert"/);
  assert.match(html, /Try again/);
  assert.doesNotMatch(html, /founder|You don’t have any subscriptions/i);
});

test('a configured claim deadline remains visible', async () => {
  const html = await render([{ ...direct, claim_deadline: '2027-01-01T00:00:00Z' }],
    { main_world_id: 12, founders_enabled: true });
  assert.match(html, /Automatically claimed on/);
});

test('world artwork is decorative and optional; access and controls do not depend on it', async () => {
  const withArt = await render([{ ...direct, world_background: '/realm-banner.jpg' }], null);
  assert.match(withArt, /src="\/realm-banner.jpg"[^>]*alt=""[^>]*loading="lazy"/);
  const withoutArt = await render([{ ...direct, world_background: null }], null);
  assert.doesNotMatch(withoutArt, /class="pass-ticket-art"/);
  for (const html of [withArt, withoutArt]) {
    assert.match(html, /Phalanx Founder Pass/);
    assert.match(html, /Complimentary access/);
    assert.match(html, /Claim on this account/);
  }
});
