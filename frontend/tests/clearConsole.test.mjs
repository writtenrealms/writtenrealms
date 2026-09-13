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
      if (id === "\0test-game-router") return "export default {}";
    },
  }],
});
after(() => server.close());
const { default: game } = await server.ssrLoadModule("/src/store/modules/game.ts");
const { default: Console } = await server.ssrLoadModule("/src/components/game/console/Console.vue");
const { pendingTurnCommandState } = await server.ssrLoadModule("/src/core/instanceTimeControl.ts");

const harness = (overrides = {}) => {
  const sent = [];
  const store = createStore({ modules: { game: {
    ...game,
    state: { ...structuredClone(game.state), player: { key: "player.1" },
      player_config: { display_chat: true }, ...overrides },
    actions: { ...game.actions, sendWSMessage: (_, payload) => { sent.push(payload); return false; } },
  } } });
  const add = (message) => store.commit("game/message_add", { type: "test.message", data: {}, ...message });
  const visible = () => store.getters["game/consoleMessages"];
  return { store, sent, add, visible };
};

test("clear empties the desktop and mobile console without an echo or server request", async () => {
  for (const is_mobile of [false, true]) {
    for (const command of ["clear", "  ClEaR\t", { cmd: "clear", silent: true }]) {
      const { store, sent, add, visible } = harness({ is_mobile });
      add({ text: "Previous room description" });
      add({ text: "Previous combat hit" });
      store.commit("game/last_viewed_room_message_set", { type: "test.message" });
      store.commit("game/last_message_set", { type: "test.message" });
      await store.dispatch("game/cmd", command);
      assert.deepEqual(visible(), []);
      assert.deepEqual(store.state.game.messages, []);
      assert.equal(store.state.game.last_viewed_room_message, null);
      assert.deepEqual(store.state.game.last_message, {});
      assert.deepEqual(sent, []);
      const html = await renderToString(createSSRApp(Console).use(store));
      assert.doesNotMatch(html, /Previous|class="message /);
      add({ text: "A new message" });
      assert.deepEqual(visible().map(message => message.text), ["A new message"]);
      await store.dispatch("game/cmd", "clear");
      await store.dispatch("game/cmd", "clear");
      assert.deepEqual(visible(), []);
    }
  }
});

test("clear preserves gameplay, separate logs and event deduplication even while disconnected", async () => {
  const { store, sent, add } = harness({
    is_connected: false, room: { key: "room.1" }, map: { visited: [1] },
    com_list: [{ text: "Saved chat" }], instance_time_control: { paused: true, can_advance: true },
    time_control_request: { request_id: "advance-1" },
  });
  add({ text: "Old output" });
  store.commit("game/event_id_seen", "event-1");
  const before = { ...store.state.game };
  await store.dispatch("game/cmd", "clear");
  for (const key of ["player", "room", "map", "combat", "com_list", "instance_time_control",
    "time_control_request", "received_event_ids", "received_event_id_order"]) {
    assert.equal(store.state.game[key], before[key], key);
  }
  assert.deepEqual(sent, []);
});

test("cleared pending receipts still block advance and accept later acknowledgements", async () => {
  for (const phase of ["sending", "received", "accepted", "unconfirmed"]) {
    const { store, add, visible } = harness();
    add({ text: "north", echo: true, request_id: "pending-1",
      command_receipt: { phase, compact: false, segments: {} } });
    add({ text: "Old output" });
    const before = pendingTurnCommandState(store.state.game.messages);
    await store.dispatch("game/cmd", "clear");
    assert.deepEqual(visible(), []);
    assert.equal(pendingTurnCommandState(store.state.game.messages), before);
    store.commit("game/command_receipt_update", {
      request_id: "pending-1", phase: "accepted", compact: true,
    });
    assert.equal(pendingTurnCommandState(store.state.game.messages), null);
    assert.deepEqual(visible(), []);
    await store.dispatch("game/cmd", "clear");
    assert.deepEqual(store.state.game.messages, []);
  }
});

test("only the standalone clear command is intercepted", async () => {
  for (const command of ["say clear", "clear target", "clear;look", "clearing"]) {
    const { store, sent, add, visible } = harness();
    add({ text: "Old output" });
    await store.dispatch("game/cmd", command);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].text, command);
    assert.equal(visible()[0].text, "Old output");
  }
});
