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
  plugins: [{
    name: "test-game-router",
    enforce: "pre",
    resolveId(id) {
      if (id.endsWith("/src/router")) return "\0test-game-router";
    },
    load(id) {
      if (id === "\0test-game-router") return "export default { push() {} }";
    },
  }],
});
after(() => server.close());
const { default: game } = await server.ssrLoadModule("/src/store/modules/game.ts");
const { default: Console } = await server.ssrLoadModule("/src/components/game/console/Console.vue");
const { default: AccessibleGame } = await server.ssrLoadModule("/src/components/game/AccessibleGame.vue");

const room = { id: 1, name: "Watch House Cell", description: "A barred cell.",
  inventory: [], chars: [], details: [], actions: [], quest_callouts: [] };
const notice = { type: "cmd.enter.success", text: "New Instance ID: orientation-run",
  data: { world_id: 20, instance_ref: "orientation-run", first_entry: true } };
const returningNotice = { ...notice, text: "Resuming Instance ID: orientation-run",
  data: { ...notice.data, first_entry: false } };
const snapshot = (world = {}, type = "cmd.state.sync.success") => ({
  type, data: { room, world: { id: 20, instance_ref: "orientation-run", ...world } },
});
const harness = (is_mobile = false) => {
  const store = createStore({ modules: { game: {
    ...game,
    state: { ...structuredClone(game.state), is_mobile, player: {}, player_config: {}, room },
  } } });
  const add = message => store.commit("game/message_add", message);
  const render = () => renderToString(createSSRApp(Console).use(store));
  return { store, add, render };
};

test("instance ID follows enter and precedes the greeting once, in every entry mode", async () => {
  for (const mobile of [false, true]) {
    for (const mode of ["all", "reveal", "replace"]) {
      const { store, add, render } = harness(mobile);
      add({ type: "cmd.text", text: "enter", echo: true });
      add(notice);
      add({ type: "cmd./echo.success", text: "The watchman greets you." });
      const incoming = snapshot({ entry_message: "The watchman greets you.", entry_message_mode: mode });
      add(incoming);
      const html = await render();
      assert.equal(html.match(/Instance ID: orientation-run/g)?.length, 1);
      assert.ok(html.indexOf(">enter<") < html.indexOf("Instance ID:"));
      assert.ok(html.indexOf("Instance ID:") < html.indexOf("The watchman greets you."));
      assert.match(html, /class="[^\"]*\bmessage\b[^\"]*\bcmd\.enter\.success\b[^\"]*\bgrouped\b[^\"]*"/);
      assert.equal(store.state.game.messages.at(-1).instance_ref_announced, true);
      assert.equal(incoming.instance_ref_announced, undefined);
      assert.equal(store.state.game.pending_instance_entry, null);
    }
  }
});

test("snapshots without a matching pending announcement retain the instance ID", async () => {
  for (const type of ["cmd.state.sync.success", "system.connect.success"]) {
    const { store, add, render } = harness();
    add(snapshot({}, type));
    assert.match(await render(), /Instance ID: orientation-run/);
    add(notice);
    add(snapshot({ id: 21, instance_ref: "another-run" }, type));
    assert.match(await render(), /Instance ID: another-run/);
    assert.equal(store.state.game.messages.at(-1).instance_ref_announced, false);
    add(snapshot({}, type));
    assert.equal(store.state.game.messages.at(-1).instance_ref_announced, false);
  }
});

test("initial login shows the instance ID, opening RP, then room without an enter command", async () => {
  for (const mobile of [false, true]) {
    const { add, render } = harness(mobile);
    add({ type: "system.connect.success", data: {} });
    add(notice);
    add({ type: "notification./echo", text: "Tally marks cover the wall." });
    add({ type: "notification.cmd.say.success", text: "The watchman greets you." });
    add({ type: "notification./echo", text: "Orders rise outside." });
    add(snapshot());
    const html = await render();
    assert.equal(html.match(/Instance ID: orientation-run/g)?.length, 1);
    const positions = ["Instance ID:", "Tally marks cover the wall.",
      "The watchman greets you.", "Orders rise outside.", "Watch House Cell"]
      .map(text => html.indexOf(text));
    assert.ok(positions.every(position => position >= 0));
    assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
  }
});

test("return narration preserves blank paragraph lines without adding a trailing blank line", async () => {
  for (const mobile of [false, true]) {
    const { store, add, render } = harness(mobile);
    add(returningNotice);
    add({ type: "notification.instance.introduction", text:
      "Tally marks cover the wall.\n\nThe watchman greets you.\n \nOrders rise outside.\n" });
    add({ ...snapshot(), text: "Watch House Cell" });
    for (const html of [await render(), await renderToString(createSSRApp(AccessibleGame).use(store))]) {
      const narration = html.slice(html.indexOf("Tally marks cover the wall."), html.indexOf("Watch House Cell"));
      assert.equal(narration.match(/<br\b/g)?.length, 2);
      assert.match(narration, /Tally marks cover the wall\.[\s\S]*<br[^>]*>[\s\S]*The watchman greets you\.[\s\S]*<br[^>]*>[\s\S]*Orders rise outside\./);
    }
  }
});

test("reentering the same run announces each arrival without duplicating its snapshot ID", async () => {
  const { add, render } = harness();
  for (let entry = 0; entry < 2; entry += 1) {
    add({ type: "cmd.text", text: "enter", echo: true });
    add(entry === 0 ? notice : returningNotice);
    add(snapshot());
    add(snapshot({ id: 1, instance_ref: null }));
  }
  const html = await render();
  assert.equal(html.match(/New Instance ID: orientation-run/g)?.length, 1);
  assert.equal(html.match(/Resuming Instance ID: orientation-run/g)?.length, 1);
});

test("new and resumed labels render once before the room with no introduction", async () => {
  for (const mobile of [false, true]) {
    for (const announcement of [notice, returningNotice]) {
      const { store, add, render } = harness(mobile);
      add(announcement);
      add({ ...snapshot(), text: 'Watch House Cell' });
      for (const html of [await render(), await renderToString(createSSRApp(AccessibleGame).use(store))]) {
        assert.equal(html.split(announcement.text).length - 1, 1);
        assert.ok(html.indexOf(announcement.text) < html.indexOf('Watch House Cell'));
      }
    }
  }
});

test("completed status remains visible after an early ID announcement", async () => {
  const { add, render } = harness();
  add(returningNotice);
  add(snapshot({ instance_status: "completed" }));
  const html = await render();
  assert.equal(html.match(/Instance ID: orientation-run/g)?.length, 1);
  assert.match(html, /Status: Completed/);
  assert.match(html, /Resuming Instance ID: orientation-run/);
});

test("clearing the console also clears an unmatched entry announcement", async () => {
  const { store, add, render } = harness();
  add(notice);
  store.commit("game/messages_clear");
  add(snapshot());
  assert.match(await render(), /Instance ID: orientation-run/);
});

test("connection messages distinguish new visits, returns, and brief reconnects", async (t) => {
  t.mock.method(console, 'log', () => {});
  for (const mobile of [false, true]) {
    let callbacks;
    const sent = [];
    const store = createStore({
      modules: {
        auth: { state: { token: 'test-token' } },
        game: {
          ...game,
          state: { ...structuredClone(game.state), is_mobile: mobile, player_id: 1,
            player_config: {}, websocket: { readyState: 1, send: message => sent.push(JSON.parse(message)) } },
          mutations: { ...game.mutations, openWS: (_state, handlers) => { callbacks = handlers; } },
        },
      },
      mutations: { 'ui/notification_set': () => {} },
    });
    await store.dispatch('game/openWebSocket');
    callbacks.onopen();
    assert.equal(sent.at(-1).data.reconnect, false);
    const receive = message => callbacks.onmessage({ data: JSON.stringify(message) });
    const fullSnapshot = (kind, id = 20) => ({
      ...snapshot({ id, entry_message: 'Opening scene.' }),
      data: { ...snapshot({ id, entry_message: 'Opening scene.' }).data,
        actor: { id: 1, key: 'player.1', name: 'Fen', config: {} }, map: [], who_list: [],
        ...(kind ? { instance_entry: kind } : {}),
      },
    });

    // A fresh browser session returning to a saved run must remain playable.
    await receive(returningNotice);
    await receive({ type: 'notification.instance.introduction', text: 'Opening scene.', data: {} });
    await receive(fullSnapshot('return'));
    assert.equal(store.state.game.world_entry.message, null);
    assert.equal(store.state.game.has_received_state, true);
    assert.equal(store.state.game.full_screen_message, '');
    assert.ok(!store.state.game.transfer_to.world_name);
    const html = await renderToString(createSSRApp(Console).use(store).directive('interactive', {}));
    assert.equal(html.match(/Instance ID: orientation-run/g)?.length, 1);
    assert.match(html, /Resuming Instance ID: orientation-run/);
    assert.ok(html.indexOf('Instance ID:') < html.indexOf('Opening scene.'));
    assert.ok(html.indexOf('Opening scene.') < html.indexOf('Watch House Cell'));

    callbacks.onclose();
    callbacks.onopen();
    assert.equal(sent.at(-1).data.reconnect, true);
    await receive(fullSnapshot('sync'));
    assert.equal(store.state.game.world_entry.message, null);
    assert.equal(store.state.game.messages.filter(m => m.type === 'notification.instance.introduction').length, 1);

    // Physical first entry gets a modal; returning to that run does not.
    await receive({ ...notice, data: { ...notice.data, world_id: 21 } });
    await receive(fullSnapshot(undefined, 21));
    assert.ok(store.state.game.world_entry.message);
    store.commit('game/world_entry_dismiss');
    await receive(returningNotice);
    await receive(fullSnapshot(undefined));
    assert.equal(store.state.game.world_entry.message, null);
    store.commit('game/reset_state');
    assert.equal(store.state.game.has_received_state, false);
  }
});
