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
const { default: Console } = await server.ssrLoadModule("/src/components/game/console/Console.vue");
const player = { id: 1, key: "player.1", name: "Tidecaller", keyword: "tidecaller" };
const mob = { key: "mob.2", name: "a headsman", keyword: "headsman" };
const room = {
  name: "A Persian Camp", description: "Rows of tents cover the dusty ground.", north: "room.2",
  inventory: [{ key: "item.1", name: "a shield", room_description: "A shield lies here." }],
  chars: [player, { ...mob, room_description: "A headsman stands guard." }],
  details: [], actions: [], quest_callouts: [],
};
const attack = {
  type: "notification.combat.attack", text: "You hit a headsman for 59 damage.",
  data: { actor: player, target: mob, attack: "attack", label: "Attack", outcome: "hit",
    damage_taken: 59, damage_absorbed: 0, is_crit_hit: false, is_heal: false, round_id: "round-1" },
};
// WR2 sends resource/stat labels; there are no WR1 attacks/effects maps.
const world = { labels: { resources: {}, attributes: {}, stats: {}, classes: {}, order: {} } };
const render = (messages, preferences = {}, is_mobile = false, worldOverride = world) => (
  renderToString(createSSRApp(Console).use(createStore({
    state: { game: { is_mobile, player_id: player.id, player, room, world: worldOverride,
      player_config: { room_brief: false, combat_brief: false, ...preferences },
      last_viewed_room_message: null, motd: "" } },
    getters: { "game/consoleMessages": () => messages.map((message, index) => ({
      ...message, message_id: String(index),
    })) },
  })).directive("interactive", {}))
);
const plainText = html => html.replace(/<[^>]*>/g, "").replace(/\s+/g, " ");

test("room and combat brief modes work independently and together with WR2 messages", async () => {
  for (const is_mobile of [false, true]) {
    for (const room_brief of [false, true]) {
      for (const combat_brief of [false, true]) {
        const html = await render([
          { type: "cmd.move.success", data: { room } }, attack,
        ], { room_brief, combat_brief }, is_mobile);
        assert.equal(html.includes(room.description), !room_brief);
        assert.equal(html.includes(attack.text), !combat_brief);
        assert.equal(html.includes("59 dmg"), combat_brief);
        assert.match(html, /A Persian Camp/);
        assert.match(html, /exits: N/);
        assert.match(html, /A shield lies here/);
        assert.match(html, /A headsman stands guard/);
        if (combat_brief) assert.match(plainText(html), /\[ Attack \] Tidecaller .* Headsman/);
      }
    }
  }
});

test("room brief hides automatic room descriptions but explicit look always shows them", async () => {
  for (const type of ["cmd.move.success", "cmd.flee.success", "affect.transfer",
    "cmd.state.sync.success", "system.connect.success", "affect.death", "cmd./jump.success",
    "cmd.look.success"]) {
    const html = await render([{ type, data: { room, target: room, target_type: "room", world: {} } }],
      { room_brief: true });
    assert.equal(html.includes(room.description), type === "cmd.look.success", type);
    assert.match(html, /A Persian Camp/);
    assert.match(html, /A shield lies here/);
  }
});

test("brief attacks retain authored labels, damage, absorption, dodges, crits and healing", async () => {
  const messages = [
    { ...attack, data: { ...attack.data, label: "Trident", is_crit_hit: true, damage_absorbed: 12 } },
    { ...attack, data: { ...attack.data, outcome: "dodged", damage_taken: 0 } },
    { ...attack, type: "notification.combat.healing", data: { ...attack.data,
      label: "Wellspring", target: player, is_heal: true, healing_done: 42 } },
  ];
  const html = await render(messages, { combat_brief: true });
  assert.match(plainText(html), /\[ Trident \].*\(12 abs\).*59 dmg.*!/);
  assert.match(html, /\(dodge\)/);
  assert.match(html, /0 dmg/);
  assert.match(plainText(html), /\[ Wellspring \].*42 .*hp/);
});

test("brief attacks render a useful fallback when optional label metadata is absent", async () => {
  for (const worldOverride of [{}, { labels: {} }]) {
    const html = await render([{ ...attack, data: { ...attack.data, label: null } }],
      { combat_brief: true }, false, worldOverride);
    assert.match(plainText(html), /\[ Attack \]/);
  }
});

test("WR2 combat effects use their event label, target and round duration in brief mode", async () => {
  const effect = { type: "notification.combat.effect", text: "You apply Crest to yourself.",
    data: { actor: player, target: player, label: "Crest", effect: "crest", duration_rounds: 3 } };
  assert.match(await render([effect]), /You apply Crest to yourself/);
  const html = await render([effect], { combat_brief: true });
  assert.match(plainText(html), /\[ Crest \] Effect Start.*3 rds/);
  assert.doesNotMatch(html, /You apply Crest|sec/);
  const targetHtml = await render([{ ...effect,
    data: { ...effect.data, target: mob, duration_rounds: 1 } }], { combat_brief: true });
  assert.match(plainText(targetHtml), /Headsman Effect Start.*1 rd/);
});

test("timed effect messages also tolerate WR2 worlds without an effects label map", async () => {
  const html = await render([{ type: "effect.start", text: "A ward forms.", data: {
    target: mob.key, target_data: mob, code: "ward", name: "Ward", duration: 10, friendly: true,
  } }], { combat_brief: true });
  assert.match(plainText(html), /\[ Ward \] Headsman Effect Start.*10 sec/);
});
