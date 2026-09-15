import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import axios from "axios";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { default: Completions } = await server.ssrLoadModule("/src/components/builder/world/PlayerInstanceCompletions.vue");
const { formatClearTime, fetchPlayerCompletions } = await server.ssrLoadModule("/src/services/playerCompletions.ts");
const render = (props = {}) => renderToString(createSSRApp(Completions, {
  completions: [], loading: false, error: false, hasMore: false, ...props,
}));

test("completion rows show template, timestamp, exact clear time and timing mode", async () => {
  const html = await render({ completions: [
    { id: 1, template_name: 'A Persian Outpost', completed_at: '2026-09-15T21:45:00Z', clear_time_ms: 1125309, time_control: true },
    { id: 2, template_name: '<script>unsafe</script>', completed_at: '2026-09-14T21:45:00Z', clear_time_ms: 3600000, time_control: false },
  ] });
  assert.match(html, /Instance Completions/);
  assert.match(html, /A Persian Outpost/);
  assert.match(html, /datetime="2026-09-15T21:45:00Z"/);
  assert.match(html, /18m 45\.309s/);
  assert.match(html, /1h 0m 0\.000s/);
  assert.match(html, /Time control/);
  assert.match(html, /Continuous/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
});

test("loading, empty, retry, and pagination states are distinct", async () => {
  assert.match(await render(), /No instance completions recorded/);
  const loading = await render({ loading: true, hasMore: true });
  assert.match(loading, /Loading completions/);
  assert.doesNotMatch(loading, /No instance completions recorded/);
  assert.match(loading, /<button[^>]*disabled/);
  const error = await render({ error: true, hasMore: true });
  assert.match(error, /role="alert"/);
  assert.match(error, /RETRY/);
  assert.doesNotMatch(error, /No instance completions recorded|LOAD MORE/);
  assert.match(await render({ hasMore: true }), /LOAD MORE/);
});

test("clear times preserve milliseconds across minute and hour boundaries", () => {
  for (const [time, expected] of [[0, '0.000s'], [1, '0.001s'], [59999, '59.999s'], [60000, '1m 0.000s'], [3600001, '1h 0m 0.001s']]) {
    assert.equal(formatClearTime(time), expected);
  }
});

test("pagination sends only the cursor to the current player's endpoint", async () => {
  const original = axios.get;
  const calls = [];
  axios.get = async (...args) => { calls.push(args); return { data: { results: [], next: null, previous: null } }; };
  try {
    await fetchPlayerCompletions(23, 25);
    await fetchPlayerCompletions(23, 25, 'http://api.invalid/builder/worlds/23/players/25/completions/?cursor=abc%3D');
    assert.deepEqual(calls, [
      ['/builder/worlds/23/players/25/completions/', { params: {} }],
      ['/builder/worlds/23/players/25/completions/', { params: { cursor: 'abc=' } }],
    ]);
  } finally { axios.get = original; }
});
