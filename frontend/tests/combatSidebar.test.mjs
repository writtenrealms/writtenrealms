import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parse } from "@vue/compiler-sfc";

const component = async path => parse(await readFile(new URL(path, import.meta.url), "utf8")).descriptor;
const sidebar = await component("../src/components/game/sidebar/Sidebar.vue");
const leftCombat = await component("../src/components/game/panel/Combat.vue");
const roster = await component("../src/components/game/sidebar/CombatRoster.vue");

test("the encounter roster belongs only to the right sidebar", () => {
  assert.match(sidebar.template.content, /<CombatRoster\s*\/>/);
  assert.doesNotMatch(sidebar.template.content, /<Chars\s*\/?>|IN ROOM/);
  assert.doesNotMatch(leftCombat.template.content, /roster|Your side|Opponents|Round/);
  assert.match(leftCombat.template.content, /class="combat-view"/);
  assert.match(leftCombat.template.content, /doAction\('flee'\)/);
  assert.match(leftCombat.template.content, /doAction\('loot'\)/);
});

test("both logs are directly available as keyboard-accessible buttons in a split action bar", () => {
  const logs = sidebar.template.content.match(/<div class="sidebar-element logs"[^>]*>([\s\S]*?)<\/div>/);
  assert.ok(logs);
  assert.doesNotMatch(sidebar.template.content, /<details|<summary/);
  assert.match(logs[1], /<button type="button"[^>]*aria-label="Quest Log"[^>]*@click="onClickQuestLog">QUESTS<\/button>/);
  assert.match(logs[1], /<button type="button"[^>]*aria-label="Communication Log"[^>]*@click="onClickCommunicationLog">COMS<\/button>/);
  assert.match(logs[1], /<template v-if="world.is_multiplayer">\s*<span[^>]*aria-hidden="true">\|<\/span>/);
  assert.match(sidebar.styles[0].content, /&\.logs\s*\{\s*display: flex;/);
  assert.match(sidebar.styles[0].content, /white-space: nowrap/);
});

test("the roster keeps target selection, paused rounds, and a quiet empty state", () => {
  assert.match(roster.template.content, /No active combat\./);
  assert.match(roster.template.content, /combat.status === 'paused'/);
  assert.match(roster.template.content, /:disabled="member.relation !== 'enemy'"/);
  assert.match(roster.template.content, /:aria-pressed=/);
  assert.match(roster.template.content, /`kill \$\{member.key\}`/);
  assert.match(roster.styles[0].content, /max-height:\s*260px/);
  assert.doesNotMatch(roster.styles[0].content, /width:\s*285px/);
});
