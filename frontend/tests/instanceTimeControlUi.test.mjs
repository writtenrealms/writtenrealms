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
const { default: CommandBar } = await server.ssrLoadModule("/src/components/game/desktop/CommandBar.vue");
const { default: Input } = await server.ssrLoadModule("/src/components/game/Input.vue");
const control = {
  enabled: true, pause_in_combat: true, paused: true, clock_offset_seconds: 0, tick: 7, generation: 3,
  pending_revision: 11, simulation_time: "2026-09-05T00:00:14Z",
  pending_command: { command_type: "move", label: "north" }, can_advance: true, run_id: 14,
};
const render = (overrides = {}, component = TimeControl) => renderToString(createSSRApp(component).use(createStore({
  state: { game: { instance_time_control: control, time_control_request: null,
    time_control_error: "", is_connected: true, messages: [], ...overrides } },
})));
const advanceButton = (html) => html.match(/<button[^>]*class="[^"]*advance-turn[^"]*"[^>]*>/)?.[0];

const inputHarness = async (overrides = {}) => {
  const commands = [];
  const store = createStore({
    state: { game: { instance_time_control: control, time_control_request: null,
      is_connected: true, messages: [], ...overrides } },
    actions: {
      'game/cmd': (_, payload) => commands.push({ action: 'game/cmd', payload }),
      'game/time_control_command': (_, payload) => commands.push({ action: 'game/time_control_command', payload }),
    },
  });
  let bindings;
  // Exercise Input's real submit handler in a Vue injection/lifecycle context.
  // SSR leaves its browser-only focus and keyboard listeners unmounted.
  await renderToString(createSSRApp({ setup() {
    bindings = Input.setup({}, { expose() {} });
    return () => null;
  } }).use(store));
  return { commands, submit(text) { bindings.input.value = text; bindings.onSubmit(); } };
};

test("empty Enter advances paused combat with or without a queued action", async () => {
  for (const pending_command of [control.pending_command, null]) {
    const { commands, submit } = await inputHarness({ instance_time_control: { ...control, pending_command } });
    submit('look');
    assert.deepEqual(commands.shift(), { action: 'game/cmd', payload: 'look' });
    for (const text of ['', '   ']) {
      submit(text);
      assert.deepEqual(commands.shift(), { action: 'game/time_control_command', payload: {
        type: 'cmd.advance', text: 'advance', data: {
          run_id: 14, expected_tick: 7, expected_generation: 3, expected_pending_revision: 11,
        },
      } });
    }
  }
});

test("empty Enter still repeats the last command during ordinary timing", async () => {
  for (const instance_time_control of [null, { ...control, paused: false, can_advance: false }]) {
    const { commands, submit } = await inputHarness({ instance_time_control });
    submit('north');
    submit('');
    assert.deepEqual(commands, [
      { action: 'game/cmd', payload: 'north' },
      { action: 'game/cmd', payload: 'north' },
    ]);
  }
});

test("blocked empty Enter neither advances nor replays the previous command", async () => {
  for (const state of [
    { is_connected: false },
    { time_control_request: { request_id: 'advance', label: 'advance' } },
    { messages: [{ echo: true, command_receipt: { phase: 'received' } }] },
    { messages: [{ echo: true, command_receipt: { phase: 'unconfirmed' } }] },
    { instance_time_control: { ...control, can_advance: false } },
  ]) {
    const { commands, submit } = await inputHarness(state);
    submit('tri');
    submit('');
    assert.deepEqual(commands, [{ action: 'game/cmd', payload: 'tri' }]);
  }
});

test("ordinary worlds render no instance turn controls", async () => {
  assert.doesNotMatch(await render({ instance_time_control: null }), /Advance Turn/);
});

test("a waiting run exposes its prepared action and enabled advance", async () => {
  const html = await render();
  assert.match(html, /Combat paused/);
  assert.doesNotMatch(html, /Turn 8/);
  assert.match(html, /Action: north/);
  assert.match(html, /Clear action/);
  assert.doesNotMatch(advanceButton(html), /disabled/);
});

const actionButton = (html) => html.match(/<button[^>]*class="instance-action"[^>]*>/)?.[0];
const clearButton = (html) => html.match(/<button[^>]*class="clear-instance-action"[^>]*>/)?.[0];

test("desktop keeps the action inside the command form with a separate compact pause toggle", async () => {
  const html = await render({}, CommandBar);
  assert.match(html, /<form[^>]*>[\s\S]*id="console-input"[\s\S]*class="instance-action"[\s\S]*<\/form>/);
  assert.match(html, /Action:<\/span>/);
  assert.match(html, /class="action-text"[^>]*>north<\/span>/);
  assert.match(html, /Pause Combat/);
  assert.doesNotMatch(html, /Normal world timing|Combat paused|Prepared:|Clear action|Settings/);
  assert.doesNotMatch(actionButton(html), /disabled/);
  assert.match(actionButton(html), /type="button"/);
  assert.match(actionButton(html), /aria-label="Advance turn — Action: north"/);
  assert.match(clearButton(html), /type="button"/);
  assert.match(clearButton(html), /aria-label="Clear queued action"/);
  assert.doesNotMatch(clearButton(html), /disabled/);
  assert.match(html, /class="instance-action"[\s\S]*class="clear-instance-action"[\s\S]*<\/form>/);
});

test("desktop can advance a paused round without a queued action", async () => {
  const html = await render({ instance_time_control: { ...control, pending_command: null } }, CommandBar);
  assert.match(html, /<form[^>]*>[\s\S]*id="console-input"[\s\S]*class="instance-action"[\s\S]*<\/form>/);
  assert.match(html, /class="action-text"[^>]*>Advance Turn<\/span>/);
  assert.doesNotMatch(html, /Action:<\/span>/);
  assert.match(actionButton(html), /aria-label="Advance turn without a queued action"/);
  assert.match(actionButton(html), /title="Advance turn without a queued action"/);
  assert.match(actionButton(html), /type="button"/);
  assert.doesNotMatch(actionButton(html), /disabled/);
  assert.equal(clearButton(html), undefined);
});

test("desktop restores the plain input outside paused combat", async () => {
  for (const snapshot of [null, { ...control, paused: false }]) {
    const html = await render({ instance_time_control: snapshot }, CommandBar);
    assert.equal(actionButton(html), undefined);
    assert.equal(clearButton(html), undefined);
    assert.doesNotMatch(html, /with-action/);
    assert.match(html, /id="console-input"/);
    assert.equal(/Pause Combat/.test(html), snapshot !== null);
  }
});

test("desktop clear waits for confirmed commands and a connected socket", async () => {
  for (const state of [
    { is_connected: false },
    { time_control_request: { request_id: 'cancel', label: 'cancelturn' } },
    { messages: [{ echo: true, command_receipt: { phase: 'received' } }] },
    { messages: [{ echo: true, command_receipt: { phase: 'unconfirmed' } }] },
  ]) assert.match(clearButton(await render(state, CommandBar)), /disabled/);
});

test("desktop action cannot advance while disconnected or awaiting prior commands", async () => {
  for (const pending_command of [control.pending_command, null]) {
    const snapshot = { ...control, pending_command };
    for (const state of [
      { is_connected: false },
      { time_control_request: { request_id: 'advance', label: 'advance' } },
      { messages: [{ echo: true, command_receipt: { phase: 'received' } }] },
      { messages: [{ echo: true, command_receipt: { phase: 'unconfirmed' } }] },
      { instance_time_control: { ...snapshot, can_advance: false } },
    ]) assert.match(actionButton(await render({ instance_time_control: snapshot, ...state }, CommandBar)), /disabled/);
  }
});

test("desktop action shows the resolved hotkey name and keeps full text accessible", async () => {
  const html = await render({ instance_time_control: { ...control, pending_command: {
    command_type: 'text', label: '6', payload: { _hotkey_resolution: 'crest' },
  } } }, CommandBar);
  assert.match(html, /class="action-text"[^>]*>crest<\/span>/);
  assert.match(actionButton(html), /aria-label="Advance turn — Action: crest"/);
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
