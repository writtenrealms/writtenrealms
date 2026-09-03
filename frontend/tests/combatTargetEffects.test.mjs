import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const roundEffectsSource = await readFile(
  new URL("../src/core/roundEffects.ts", import.meta.url),
  "utf8",
);
const gameStoreSource = await readFile(
  new URL("../src/store/modules/game.ts", import.meta.url),
  "utf8",
);
const combatPanelSource = await readFile(
  new URL("../src/components/game/panel/Combat.vue", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(roundEffectsSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const { presentRoundEffects } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("stun effects use a target-status label and round fill", () => {
  const effects = presentRoundEffects([
    {
      id: 41,
      effect: "stun",
      label: "Shield Bash",
      category: "debuff",
      remaining_rounds: 1,
      duration_rounds: 2,
    },
  ], { useStatusLabels: true });

  assert.deepEqual(effects, [
    {
      key: "41",
      label: "Stunned",
      category: "debuff",
      remainingRounds: 1,
      fillWidth: "50%",
      title: "Stunned: 1 of 2 rounds remaining",
    },
  ]);
});

test("expired round effects are omitted", () => {
  assert.deepEqual(presentRoundEffects([
    {
      id: 42,
      effect: "stun",
      remaining_rounds: 0,
      duration_rounds: 2,
    },
  ], { useStatusLabels: true }), []);
});

test("combat updates route combatant snapshots to the current target", () => {
  assert.match(
    gameStoreSource,
    /message_data\.data\?\.combatants/,
  );
  assert.match(
    gameStoreSource,
    /commit\("combat_target_effects_set", combatant\)/,
  );
  assert.match(
    gameStoreSource,
    /state\.player_target\.key !== payload\.target\.key/,
  );
  assert.match(combatPanelSource, /class="target-round-effect"/);
  assert.match(combatPanelSource, /useStatusLabels:\s*true/);
});
