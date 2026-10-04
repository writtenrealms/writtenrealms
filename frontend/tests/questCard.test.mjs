import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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
const { default: QuestCard } = await server.ssrLoadModule("/src/components/game/QuestCard.vue");
const { default: QuestMessage } = await server.ssrLoadModule("/src/components/game/console/QuestMessage.vue");
const { questInstanceCard, questInfoActions, questRoomActions } = await server.ssrLoadModule("/src/core/questPresentation.ts");

const quest = {
  id: 27, status: "active",
  template: { slug: "athens-intro", name: "A Debt to Athens", quest_type: "quest" },
  current_step: {
    text: { body: "The watchman needs defenders.\n\nReturn when you have finished." },
    recap: "Defeat the dummy, then return to the watchman.",
    objectives: [
      { id: "dummy", text: "Defeat the practice dummy.", status: "active", progress_current: 0, progress_target: 1 },
      { id: "report", text: "Report back.", status: "complete", progress_current: 1, progress_target: 1 },
      { id: "secret", text: "Hidden destination.", status: "hidden", progress_current: 0, progress_target: 1 },
    ],
  },
};
const renderCard = (card = questInstanceCard(quest), props = {}) => (
  renderToString(createSSRApp(QuestCard, { card, ...props }))
);
const renderMessage = (message, latest = false, context = {}) => {
  const store = createStore({ state: { game: {
    last_message: latest ? { [message.type]: message } : {}, world: {}, ...context,
  } } });
  const renderedMessage = latest ? store.state.game.last_message[message.type] : message;
  return renderToString(createSSRApp(QuestMessage, { message: renderedMessage }).use(store));
};
const hasClasses = (html, ...classes) => Array.from(html.matchAll(/class="([^"]+)"/g))
  .some((match) => classes.every((name) => match[1].split(/\s+/).includes(name)));

test("console quest info renders the shared card with identical data hierarchy", async () => {
  const consoleHtml = await renderMessage({ type: "cmd.quest.success", data: { subcommand: "info", quest } });
  const sharedHtml = await renderCard();
  const article = html => html.match(/<article[\s\S]*?<\/article>/)?.[0]
    .replace(/ data-v-[\w-]+(?:="[^"]*")?/g, "");
  assert.equal(article(consoleHtml), article(sharedHtml));
  const sections = ["A DEBT TO ATHENS", "athens-intro", "The watchman needs defenders.",
    "Defeat the dummy, then return to the watchman.", "Objectives", "Defeat the practice dummy."];
  for (let index = 1; index < sections.length; index += 1) {
    assert.ok(sharedHtml.indexOf(sections[index - 1]) < sharedHtml.indexOf(sections[index]));
  }
  assert.ok(hasClasses(sharedHtml, "quest-badge", "tone-active"));
  assert.ok(hasClasses(sharedHtml, "quest-objective-status", "complete"));
  assert.match(sharedHtml, />0\/1</);
  assert.doesNotMatch(sharedHtml, /Hidden destination/);
});

test("collapsed log cards keep their identity and accessible disclosure without rendering details", async () => {
  const html = await renderCard(undefined, {
    collapsible: true, expanded: false, detailsId: "quest-log-details-27", reference: "[ 27 ]",
  });
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /aria-controls="quest-log-details-27"/);
  assert.match(html, /id="quest-log-details-27"/);
  assert.ok(hasClasses(html, "quest-badge", "tone-active"));
  assert.match(html, /\[ 27 \]/);
  assert.doesNotMatch(html, /quest-body|quest-objectives/);
  const expanded = await renderCard(undefined, { collapsible: true, expanded: true });
  assert.match(expanded, /aria-expanded="true"/);
  assert.match(expanded, /quest-objectives/);
});

test("quest cards preserve paragraph breaks, escape authored markup, and handle untargeted progress", async () => {
  const source = structuredClone(quest);
  source.current_step.text.body = "<script>hello & goodbye</script>\n\nNext paragraph.";
  source.current_step.objectives[0].progress_target = 0;
  source.current_step.objectives[0].progress_current = 3;
  const card = questInstanceCard(source);
  assert.equal(card.objectives[0].progress, "3");
  const html = await renderCard(card);
  assert.match(html, /&lt;script&gt;hello &amp; goodbye&lt;\/script&gt;\n\nNext paragraph\./);
  assert.doesNotMatch(html, /<script>/);
});

test("console actions remain available only on the latest message", async () => {
  const message = {
    type: "quest.opportunity.presented",
    data: { opportunity: { name: "A Debt to Athens", slug: "athens-intro" } },
  };
  assert.match(await renderMessage(message, true), />ACCEPT<\/button>/);
  assert.doesNotMatch(await renderMessage(message), />ACCEPT<\/button>/);
  const choiceQuest = { ...quest, current_step: { choices: [{ id: "stay", text: "Stay here." }] } };
  const info = { type: "cmd.quest.success", data: { subcommand: "info", quest: choiceQuest } };
  assert.match(await renderMessage(info, true), /CHOOSE/);
  assert.doesNotMatch(await renderMessage(info), /CHOOSE/);
  assert.equal(questInstanceCard(choiceQuest).choiceRows[0].command, "quest choose athens-intro stay");
});

test("quest work buttons use live room availability, not command words in the recap", async () => {
  const work = { ...quest, current_step: { ...quest.current_step,
    room_action: { command: 'wash clay', room_key: 'room.114', world_id: 24 },
  } };
  const room = { key: 'room.114', actions: ['craft', 'wash clay'] };
  assert.deepEqual(questRoomActions(work, room, 24), [
    { label: 'WASH CLAY', command: 'wash clay', tone: 'primary' },
  ]);
  for (const [candidate, currentRoom, worldId] of [
    [work, { ...room, actions: [] }, 24], // Busy or no longer eligible.
    [work, { ...room, actions: ['strain clay'] }, 24], // Step advanced.
    [work, { ...room, key: 'room.115' }, 24],
    [work, room, 25],
    [work, undefined, 24],
    [{ ...work, status: 'resolved' }, room, 24],
    [{ ...quest, current_step: { recap: 'WASH CLAY in the basin.' } }, room, 24],
  ]) {
    assert.deepEqual(questRoomActions(candidate, currentRoom, worldId), []);
  }
  const message = { type: 'cmd.quest.success', data: { subcommand: 'info', quest: work } };
  const context = { room, world: { id: 24 } };
  assert.match(await renderMessage(message, true, context), />WASH CLAY<\/button>/);
  assert.doesNotMatch(await renderMessage(message, false, context), />WASH CLAY<\/button>/);
  assert.doesNotMatch(await renderMessage(message, true, {
    ...context, room: { ...room, actions: [] },
  }), />WASH CLAY<\/button>/);
  const update = await renderMessage({ type: 'quest.instance.updated', data: { quest: work } }, true, context);
  assert.match(update, />WASH CLAY<\/button>/);
  assert.match(update, />INFO<\/button>/);
});

test("resolved quests retain their badge, outcome, and optional log actions", async () => {
  const card = questInstanceCard({ ...quest, status: "resolved" });
  card.badges.push({ label: "repeatable", tone: "tone-type" });
  card.metaLines = ["Resolution: complete"];
  card.actions = [{ label: "INFO", command: "quest info athens-intro", tone: "secondary" }];
  const html = await renderCard(card, { actionable: true });
  assert.ok(hasClasses(html, "quest-badge", "tone-resolved"));
  assert.match(html, />repeatable<\/span>/);
  assert.match(html, /Resolution: complete/);
  assert.match(html, />INFO<\/button>/);
  const abandoned = await renderMessage({ type: 'quest.instance.resolved',
    data: { quest: { ...quest, status: 'resolved', resolution: 'abandoned' } },
  });
  assert.match(abandoned, /You abandon <span[^>]*class="quest-link"[^>]*>A Debt to Athens<\/span>\./);
  assert.doesNotMatch(abandoned, /<article|>ABANDON<\/button>/);
});

test("active quest info controls pair INFO with ABANDON, including after work actions", async () => {
  assert.deepEqual(questInfoActions(quest), [
    { label: 'INFO', command: 'quest info athens-intro', tone: 'secondary' },
    { label: 'ABANDON', command: 'quest abandon 27', tone: 'secondary' },
  ]);
  assert.deepEqual(questInfoActions({ ...quest, template: {} }), []);
  assert.deepEqual(questInfoActions({ ...quest, status: 'resolved' }).map(action => action.label), ['INFO']);
  const active = { ...quest, current_step: { ...quest.current_step,
    room_action: { command: 'wash clay', room_key: 'room.114', world_id: 24 },
  } };
  const context = { room: { key: 'room.114', actions: ['wash clay'] }, world: { id: 24 } };
  const update = { type: 'quest.instance.updated', data: { quest: active } };
  const html = await renderMessage(update, true, context);
  const labels = Array.from(html.matchAll(/>(WASH CLAY|INFO|ABANDON)<\/button>/g), match => match[1]);
  assert.deepEqual(labels, ['WASH CLAY', 'INFO', 'ABANDON']);
  assert.doesNotMatch(await renderMessage(update, false, context), />ABANDON<\/button>/);
  assert.doesNotMatch(await renderMessage({ type: 'quest.instance.resolved',
    data: { quest: { ...quest, status: 'resolved' } } }, true), />ABANDON<\/button>/);
});

test("quest log uses the same presentation and card component as console info", async () => {
  const source = await readFile(new URL("../src/components/game/QuestLog.vue", import.meta.url), "utf8");
  assert.match(source, /questInstanceCard\(quest\)/);
  assert.match(source, /<QuestCard[\s\S]*?@command="runQuestCommand"/);
  assert.doesNotMatch(source, /class="quest-objective"|class="quest-body"/);
});
