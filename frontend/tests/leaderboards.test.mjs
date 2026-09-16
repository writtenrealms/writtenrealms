import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';
import axios from 'axios';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { default: Panels } = await server.ssrLoadModule('/src/components/lobby/LeaderboardPanels.vue');
const { default: lobby } = await server.ssrLoadModule('/src/store/modules/lobby.ts');
const render = props => renderToString(createSSRApp(Panels, { panels: [], ...props }));
const panels = [
  { id: '0', type: 'instance_clear_time', title: 'A Persian Outpost — Fastest Clears', description: 'Personal bests · Time control', entries: [
    { id: 5, name: 'Runner', clear_time_ms: 1125309 },
  ] },
  { id: '1', type: 'dueling', title: 'Dueling', description: 'Minimum 3 completed matches', entries: [
    { id: 6, name: '<script>unsafe</script>', wins: 2, losses: 1, win_percentage: 200 / 3 },
  ] },
  { id: '2', type: 'glory_experience', title: 'Glory & Experience', description: '', entries: [
    { id: 7, name: 'Champion', glory: 12, experience: 400 },
  ] },
];

test('panels preserve configured order and display each ranking metric safely', async () => {
  const html = await render({ panels });
  assert.ok(html.indexOf('A Persian Outpost') < html.indexOf('Dueling'));
  assert.ok(html.indexOf('Dueling') < html.indexOf('Glory &amp; Experience'));
  for (const text of ['18m 45.309s', '66.7%', '2W · 1L', '12 glory', '400 XP', 'Minimum 3 completed matches']) {
    assert.ok(html.includes(text), text);
  }
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;unsafe/);
});

test('empty configured panels remain visible while disabled panels render no headings', async () => {
  const html = await render({ panels: panels.slice(0, 2).map(p => ({ ...p, entries: [], empty_message: 'No qualifying results yet.' })) });
  assert.match(html, /A Persian Outpost/);
  assert.match(html, /Dueling/);
  assert.equal(html.match(/No qualifying results yet\./g).length, 2);
  assert.doesNotMatch(await render({ panels: [] }), /<h2/);
  assert.match(await render({ error: true }), /role="alert"/);
  assert.match(await render({ error: true }), /RETRY/);
});

test('leaderboard failure does not block characters; retry recovers without reloading the world', async () => {
  const original = axios.get;
  let fail = true;
  const store = createStore({ modules: { lobby: { ...lobby, state: { ...lobby.state, leaderboards: [] } } } });
  axios.get = async url => {
    if (url.endsWith('/leaderboards/')) {
      if (fail) throw new Error('temporarily unavailable');
      return { data: { panels } };
    }
    if (url.includes('/chars/')) return { data: { results: [{ id: 1, name: 'My character' }] } };
    return { data: { id: 23 } };
  };
  try {
    await store.dispatch('lobby/initial_fetch', 23);
    assert.equal(store.state.lobby.world.id, 23);
    assert.equal(store.state.lobby.chars[0].name, 'My character');
    assert.equal(store.state.lobby.leaderboardError, true);
    fail = false;
    await store.dispatch('lobby/fetch_leaderboards', 23);
    assert.deepEqual(store.state.lobby.leaderboards, panels);
    assert.equal(store.state.lobby.leaderboardError, false);
    assert.equal(store.state.lobby.leaderboardsLoading, false);
  } finally { axios.get = original; }
});

test('a late response from the previous world cannot replace current panels', async () => {
  const original = axios.get;
  const store = createStore({ modules: { lobby: { ...lobby, state: { ...lobby.state, requestedWorldId: '23' } } } });
  let finish;
  axios.get = () => new Promise(resolve => { finish = resolve; });
  try {
    const pending = store.dispatch('lobby/fetch_leaderboards', 23);
    store.commit('lobby/requested_world_set', '24');
    store.commit('lobby/set_leaderboards', []);
    finish({ data: { panels } });
    await pending;
    assert.deepEqual(store.state.lobby.leaderboards, []);
  } finally { axios.get = original; }
});
