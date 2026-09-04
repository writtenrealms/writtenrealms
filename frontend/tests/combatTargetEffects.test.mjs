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
const statusPanelSource = await readFile(
  new URL("../src/components/game/panel/Status.vue", import.meta.url),
  "utf8",
);
const consoleHelpSource = await readFile(
  new URL("../src/components/game/console/Help.vue", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(roundEffectsSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const {
  playerRoundEffectSnapshot,
  presentRoundEffects,
  splitRoundEffectsByScope,
} = await import(
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
      roundsLabel: "1 rd",
      barrierRemaining: null,
      fillWidth: "50%",
      title: "Stunned: 1 of 2 rounds remaining",
    },
  ]);
});

test("beneficial barriers expose their rounds and remaining absorb pool", () => {
  const effects = presentRoundEffects([
    {
      id: 43,
      effect: "crest",
      label: "Crest",
      category: "buff",
      scope: "character",
      remaining_rounds: 2,
      duration_rounds: 3,
      primitives: [{ type: "damage_absorb", remaining: 18 }],
    },
  ]);

  assert.deepEqual(effects, [
    {
      key: "43",
      label: "Crest",
      category: "buff",
      remainingRounds: 2,
      roundsLabel: "2 rds",
      barrierRemaining: 18,
      fillWidth: "67%",
      title: "Crest: 2 of 3 rounds remaining; 18 barrier remaining",
    },
  ]);
});

test("combatant effect snapshots split character and encounter state", () => {
  const crest = { id: 43, effect: "crest", scope: "character" };
  const stun = { id: 44, effect: "stun", scope: "encounter" };

  assert.deepEqual(splitRoundEffectsByScope([crest, stun]), {
    characterEffects: [crest],
    encounterEffects: [stun],
  });
  assert.deepEqual(splitRoundEffectsByScope([]), {
    characterEffects: [],
    encounterEffects: [],
  });
  assert.deepEqual(playerRoundEffectSnapshot([
    {
      target: { key: "player.1" },
      active_effects: [crest, stun],
    },
  ], "player.1"), {
    characterEffects: [crest],
    encounterEffects: [stun],
  });
  assert.deepEqual(playerRoundEffectSnapshot([
    { target: { key: "player.1" }, active_effects: [] },
  ], "player.1"), {
    characterEffects: [],
    encounterEffects: [],
  });
  assert.equal(playerRoundEffectSnapshot([], "player.1"), null);
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
  assert.match(gameStoreSource, /playerRoundEffectSnapshot/);
  assert.match(gameStoreSource, /player_active_effects_set", characterEffects/);
  assert.match(gameStoreSource, /player_combat_effects_set", encounterEffects/);
  assert.match(statusPanelSource, /presentRoundEffects/);
  assert.match(statusPanelSource, /effect\.roundsLabel/);
});

test("disengage refreshes room combatants and clears or switches the target", () => {
  assert.match(gameStoreSource, /cmd\.disengage\.success/);
  assert.match(gameStoreSource, /notification\.combat\.disengage/);
  assert.match(
    gameStoreSource,
    /isCombatDisengage[\s\S]*room_chars_update[\s\S]*message_data\.data\.target[\s\S]*message_data\.data\.next_target/,
  );
  assert.match(
    gameStoreSource,
    /cmd\.disengage\.success[\s\S]*player_target_set", null[\s\S]*actor\?\.target \|\| null/,
  );
  assert.match(consoleHelpSource, /cmdHelp\('disengage'\)/);
});
