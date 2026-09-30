import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/core/charActions.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { buildCharActions, shouldShowTalkAction } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);
const player = { char_type: "player", core_faction: "greek" };

test("mobs are talkable by default and explicit false hides TALK", () => {
  for (const talkable of [undefined, true, false]) {
    const mob = { char_type: "mob", talkable };
    assert.equal(shouldShowTalkAction(player, mob, {}), talkable !== false);
    assert.equal(buildCharActions(mob, player, {}).talk, talkable !== false);
  }
  assert.equal(shouldShowTalkAction(player, player, {}), false);
  assert.equal(shouldShowTalkAction(player, null, {}), false);
});

test("talkable false overrides authored TALK actions without hiding other actions", () => {
  for (const actions of [["talk", "kill"], [{ action: "talk" }, "kill"], { talk: true, kill: true }]) {
    const result = buildCharActions({
      char_type: "mob", talkable: false, actions, is_merchant: true, is_trainer: true,
    }, player, {});
    assert.equal(result.talk, false);
    for (const action of ["kill", "list", "offer", "learn", "unlearn"]) {
      assert.equal(result[action], true);
    }
  }
});

test("talkable true preserves hostile faction filtering in either direction", () => {
  const mob = { char_type: "mob", core_faction: "persian", talkable: true };
  for (const factions of [
    { greek: { hostile: ["persian"] } },
    { persian: { hostile: ["greek"] } },
  ]) {
    assert.equal(buildCharActions(mob, player, { factions }).talk, false);
  }
  assert.equal(buildCharActions(mob, player, {}).talk, true);
});
