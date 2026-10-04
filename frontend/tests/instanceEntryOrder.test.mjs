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

const room = { id: 1, name: "Watch House Cell", description: "A barred cell.",
  inventory: [], chars: [], details: [], actions: [], quest_callouts: [] };
const notice = { type: "cmd.enter.success", text: "Instance ID: orientation-run",
  data: { world_id: 20, instance_ref: "orientation-run" } };
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

test("reentering the same run announces each arrival without duplicating its snapshot ID", async () => {
  const { add, render } = harness();
  for (let entry = 0; entry < 2; entry += 1) {
    add({ type: "cmd.text", text: "enter", echo: true });
    add(notice);
    add(snapshot());
    add(snapshot({ id: 1, instance_ref: null }));
  }
  assert.equal((await render()).match(/Instance ID: orientation-run/g)?.length, 2);
});

test("completed status remains visible after an early ID announcement", async () => {
  const { add, render } = harness();
  add(notice);
  add(snapshot({ instance_status: "completed" }));
  const html = await render();
  assert.equal(html.match(/Instance ID: orientation-run/g)?.length, 1);
  assert.match(html, /Status: Completed/);
});

test("clearing the console also clears an unmatched entry announcement", async () => {
  const { store, add, render } = harness();
  add(notice);
  store.commit("game/messages_clear");
  add(snapshot());
  assert.match(await render(), /Instance ID: orientation-run/);
});
