import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createSSRApp } from "vue";
import { createStore } from "vuex";
import { renderToString } from "vue/server-renderer";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { default: TimeControl } = await server.ssrLoadModule("/src/components/game/TimeControl.vue");
const control = {
  enabled: true, pause_in_combat: true, paused: true, clock_offset_seconds: 0, tick: 7, generation: 3,
  pending_revision: 11, simulation_time: "2026-09-05T00:00:14Z",
  pending_command: { command_type: "move", label: "north" }, can_advance: true, run_id: 14,
};
const render = (overrides = {}) => renderToString(createSSRApp(TimeControl).use(createStore({
  state: { game: { instance_time_control: control, time_control_request: null,
    time_control_error: "", is_connected: true, messages: [], ...overrides } },
})));
const advanceButton = (html) => html.match(/<button[^>]*class="[^"]*advance-turn[^"]*"[^>]*>/)?.[0];

test("ordinary worlds render no instance turn controls", async () => {
  assert.doesNotMatch(await render({ instance_time_control: null }), /Advance Turn/);
});

test("a waiting run exposes its prepared action and enabled advance", async () => {
  const html = await render();
  assert.match(html, /Combat paused/);
  assert.doesNotMatch(html, /Turn 8/);
  assert.match(html, /Prepared: north/);
  assert.match(html, /Clear action/);
  assert.doesNotMatch(advanceButton(html), /disabled/);
});

test("disconnected and in-flight runs cannot be advanced from the UI", async () => {
  for (const state of [
    { is_connected: false },
    { time_control_request: { request_id: "request", label: "advance" } },
    { messages: [{ echo: true, command_receipt: { phase: "received" } }] },
    { messages: [{ echo: true, command_receipt: { phase: "unconfirmed" } }] },
  ]) assert.match(advanceButton(await render(state)), /disabled/);
});

test("uncertain delivery and server refusals remain visible next to the controls", async () => {
  const html = await render({
    messages: [{ echo: true, command_receipt: { phase: "unconfirmed" } }],
    time_control_error: "The prepared action has changed.",
  });
  assert.match(html, /Reconnect before advancing/);
  assert.match(html, /The prepared action has changed/);
});

test("live exploration exposes a quick checkbox without an advance button", async () => {
  for (const enabled of [true, false]) {
    const html = await render({ instance_time_control: { ...control, pause_in_combat: enabled, paused: false, can_advance: false } });
    assert.match(html, /Normal world timing/);
    assert.match(html, /type="checkbox"/);
    assert.equal(advanceButton(html), undefined);
    const checkbox = html.match(/<input[^>]*type="checkbox"[^>]*>/)[0];
    assert.equal(/checked/.test(checkbox), enabled);
  }
});
