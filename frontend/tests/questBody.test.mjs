import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createSSRApp } from "vue";
import { createStore } from "vuex";
import { renderToString } from "vue/server-renderer";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null },
});
after(() => server.close());
const { default: QuestMessage } = await server.ssrLoadModule(
  "/src/components/game/console/QuestMessage.vue",
);

const renderMessage = (message) => {
  const app = createSSRApp(QuestMessage, { message });
  app.use(createStore({ state: { game: { last_message: {}, world: {} } } }));
  return renderToString(app);
};
const opportunity = (body) => ({
  type: "quest.opportunity.presented",
  data: { opportunity: { slug: "a-debt-to-athens", name: "A Debt to Athens", text: { body } } },
});
const questInfo = (body) => ({
  type: "cmd.quest.success",
  data: {
    subcommand: "info",
    quest: {
      id: 1,
      status: "active",
      template: { slug: "a-debt-to-athens", name: "A Debt to Athens" },
      current_step: { text: { body } },
    },
  },
});
const hint = (body) => ({ type: "quest.interaction.hint", data: { hint: body } });
const renderedBody = (html) => html.match(/<div class="quest-body"[^>]*>([\s\S]*?)<\/div>/)?.[1];

for (const [name, messageFor] of [["opportunity", opportunity], ["quest info", questInfo], ["hint", hint]]) {
  test(`${name} preserves authored paragraph breaks and single line breaks`, async () => {
    for (const newline of ["\n", "\r\n"]) {
      const body = ["The watchman needs defenders.", "", "Accept the quest.", "", "Rattle the bars.", "Then go east."].join(newline);
      const html = await renderMessage(messageFor(`  ${body}${newline}`));
      assert.equal(renderedBody(html), body);
    }
  });
}

test("quest bodies remain plain text, including HTML-like authored content", async () => {
  const html = await renderMessage(opportunity("<em>Defend & return.</em>\n\nThen report back."));
  assert.equal(renderedBody(html), "&lt;em&gt;Defend &amp; return.&lt;/em&gt;\n\nThen report back.");
});

test("missing and whitespace-only bodies do not leave an empty body section", async () => {
  for (const body of [undefined, null, "", " \n \n "]) {
    assert.equal(renderedBody(await renderMessage(opportunity(body))), undefined);
  }
});
