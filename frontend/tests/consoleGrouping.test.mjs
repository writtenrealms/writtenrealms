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
const { default: Console } = await server.ssrLoadModule(
  "/src/components/game/console/Console.vue",
);

const echo = (text = "quest accept athens-intro") => ({ type: "cmd.text", text, echo: true });
const quest = {
  id: 1,
  status: "active",
  template: { slug: "athens-intro", name: "A Debt to Athens" },
  current_step: { text: { body: "Defeat the practice dummy." } },
};
const started = { type: "quest.instance.started", data: { quest } };
const narration = {
  type: "door.state_changed",
  text: "The watchman turns his key and swings the barred door open.",
};
const render = (messages, is_mobile = false) => {
  const store = createStore({
    state: { game: { is_mobile, last_message: {}, world: {} } },
    getters: { "game/consoleMessages": () => messages.map((message, message_id) => ({
      ...message, message_id,
    })) },
  });
  return renderToString(createSSRApp(Console).use(store));
};
const groupedRows = (html) => Array.from(
  html.matchAll(/<div[^>]*class="([^"]+)"[^>]*>/g),
  (match) => match[1].split(/\s+/),
).filter((classes) => classes.includes("message"))
  .map((classes) => classes.includes("grouped"));

test("quest acceptance pairs with its command and keeps follow-up narration separate", async () => {
  for (const mobile of [false, true]) {
    const html = await render([echo(), started, narration], mobile);
    assert.deepEqual(groupedRows(html), [false, true, false]);
    assert.match(html, /has started\./);
    assert.match(html, /class="quest-inline quest-inline-started"/);
  }
});

test("quest info and progress replies do not add their own leading spacer", async () => {
  for (const [command, reply, contentClass] of [
    ["quest info athens-intro", {
      type: "cmd.quest.success", data: { quest, subcommand: "info" },
    }, "quest-message"],
    ["talk watchman", {
      type: "quest.instance.updated",
      data: { quest, updated_objective: { text: "Report back.", progress: "1/1", status: "complete" } },
    }, "quest-inline-updated"],
  ]) {
    const html = await render([echo(command), reply]);
    assert.deepEqual(groupedRows(html), [false, true]);
    const classes = Array.from(html.matchAll(/class="([^"]+)"/g),
      (match) => match[1].split(/\s+/)).find((classes) => classes.includes(contentClass));
    assert.ok(classes);
    assert.ok(classes.every((name) => !name.startsWith("mt-")));
  }
});

test("ordinary command successes and errors remain paired with their input", async () => {
  for (const type of ["cmd.talk.success", "cmd.move.error", "cmd.quest.error"]) {
    const html = await render([echo(), { type, text: "Command response.", data: { code: "usage" } }]);
    assert.deepEqual(groupedRows(html), [false, true]);
  }
});

test("ambient messages and consecutive commands keep their own spacing", async () => {
  const notification = { type: "notification.cmd.say.success", text: "A watchman says hello." };
  assert.deepEqual(groupedRows(await render([echo(), notification, started, echo()])),
    [false, false, false, false]);
  assert.deepEqual(groupedRows(await render([echo(), echo(), started])), [false, false, true]);
});

test("explicit groups still join related output without a command echo", async () => {
  const messages = ["First.", "Second.", "Separate."].map((text, index) => ({
    type: "room_write", text, group: index < 2 ? "scene" : "another-scene",
  }));
  assert.deepEqual(groupedRows(await render(messages)), [false, true, false]);
});
