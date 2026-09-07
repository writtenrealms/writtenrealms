import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const loadModule = async (path) => {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
};
const { applyInstanceTimeControl, advanceTurnCommand, combatPauseCommand, simulationTimeMs, gameplayTimeMs, pendingTurnCommandState } = await loadModule("../src/core/instanceTimeControl.ts");
const { instanceConfigFlags, setInstanceConfigFlag } = await loadModule("../src/core/instanceConfig.ts");
const snapshot = (overrides = {}) => ({
  enabled: true, pause_in_combat: true, paused: true, clock_offset_seconds: 0, tick: 7, generation: 3,
  pending_revision: 11, simulation_time: "2026-09-05T00:00:14Z",
  pending_command: null, can_advance: true, run_id: 14, ...overrides,
});

test("stale pace, turn, and prepared-action events cannot roll instance state back", () => {
  const current = snapshot();
  for (const outdated of [{ generation: 2 }, { tick: 6 }, { pending_revision: 10 }]) {
    assert.equal(applyInstanceTimeControl(current, snapshot(outdated)), current);
  }
  const prepared = snapshot({ pending_revision: 12, pending_command: { command_type: "move", label: "north" } });
  assert.equal(applyInstanceTimeControl(current, prepared), prepared);
  assert.equal(applyInstanceTimeControl(prepared, current), prepared);
});

test("leaving clears the controls and a different run starts from its own clock", () => {
  assert.equal(applyInstanceTimeControl(snapshot(), null), null);
  assert.equal(applyInstanceTimeControl(snapshot(), { enabled: false }), null);
  const nextRun = snapshot({ run_id: 15, tick: 0, generation: 0, pending_revision: 0 });
  assert.equal(applyInstanceTimeControl(snapshot(), nextRun), nextRun);
});

test("delayed events from another runtime world cannot restore or clear this run", () => {
  const current = snapshot({ world_id: 25 });
  assert.equal(applyInstanceTimeControl(current, snapshot({ world_id: 24 }), 25), current);
  assert.equal(applyInstanceTimeControl(current, { enabled: false, world_id: 24 }, 25), current);
  assert.equal(applyInstanceTimeControl(null, current, 26), null);
});

test("advancement captures the displayed run, turn, pace and prepared action version", () => {
  const current = snapshot();
  const command = advanceTurnCommand(current);
  assert.deepEqual(command, { type: "cmd.advance", text: "advance", data: {
    run_id: 14, expected_tick: 7, expected_generation: 3, expected_pending_revision: 11,
  } });
  current.pending_revision = 12;
  assert.equal(command.data.expected_pending_revision, 11);
});

test("pause and resume commands carry the current preference version", () => {
  assert.deepEqual(combatPauseCommand(snapshot(), false), {
    type: "cmd.time_control", text: "resume", data: { pause_in_combat: false, run_id: 14, expected_generation: 3 },
  });
  assert.equal(combatPauseCommand(snapshot(), true).data.pause_in_combat, true);
});

test("controlled timers use the server simulation clock without wall-time interpolation", () => {
  assert.equal(simulationTimeMs(snapshot()), Date.parse("2026-09-05T00:00:14Z"));
  assert.equal(simulationTimeMs(null), null);
  assert.equal(simulationTimeMs(snapshot({ paused: false })), null);
  assert.equal(simulationTimeMs(snapshot({ simulation_time: "invalid" })), null);
});

test("an advance cannot overtake an unacknowledged or uncertain prepared command", () => {
  const command = (phase, compact = false) => ({ echo: true, command_receipt: { phase, compact } });
  for (const phase of ["sending", "received", "accepted"]) {
    assert.equal(pendingTurnCommandState([command(phase)]), "awaiting");
  }
  assert.equal(pendingTurnCommandState([command("received"), command("unconfirmed")]), "uncertain");
  assert.equal(pendingTurnCommandState([command("accepted", true), command("cancelled")]), null);
  assert.equal(pendingTurnCommandState([{ echo: false, command_receipt: { phase: "received" } }]), null);
});

const yaml = "apiVersion: wr.io/v1\nkind: world\nspec:\n  name: A quiet cave\n  instance_single_player: true\n  instance_time_control: true\n  initial_state:\n    visits: 0\n";

test("builder controls preserve other YAML and enforce the single-player prerequisite", () => {
  assert.deepEqual(instanceConfigFlags(yaml), { singlePlayer: true, timeControl: true, editable: true });
  const disabled = setInstanceConfigFlag(yaml, "instance_single_player", false);
  assert.deepEqual(instanceConfigFlags(disabled), { singlePlayer: false, timeControl: false, editable: true });
  assert.match(disabled, /  initial_state:\n    visits: 0\n$/);
  assert.equal(setInstanceConfigFlag(disabled, "instance_time_control", true), disabled);
  assert.equal(setInstanceConfigFlag(disabled, "instance_single_player", true), yaml.replace("instance_time_control: true", "instance_time_control: false"));
  assert.equal(setInstanceConfigFlag(yaml.replaceAll("\n", "\r\n"), "instance_single_player", false), disabled.replaceAll("\n", "\r\n"));
});

test("custom YAML formatting stays intact for direct editing", () => {
  const custom = "kind: world\nspec: {instance_single_player: true, instance_time_control: true}\n";
  assert.equal(instanceConfigFlags(custom).editable, false);
  assert.equal(setInstanceConfigFlag(custom, "instance_single_player", false), custom);
});

 test("elapsed UI time excludes thinking time across pause and resume", () => {
  const live = snapshot({ paused: false, clock_offset_seconds: 0 });
  const start = Date.parse("2026-09-05T00:00:00Z");
  const paused = snapshot({ simulation_time: "2026-09-05T00:00:10Z" });
  assert.equal(gameplayTimeMs(live, start + 10000), start + 10000);
  assert.equal(gameplayTimeMs(paused, start + 100000), start + 10000);
  const stepped = { ...paused, simulation_time: "2026-09-05T00:00:12Z" };
  assert.equal(gameplayTimeMs(stepped, start + 100000), start + 12000);
  const resumed = { ...stepped, paused: false, clock_offset_seconds: 88 };
  assert.equal(gameplayTimeMs(resumed, start + 100000), start + 12000);
  assert.equal(gameplayTimeMs(resumed, start + 103000), start + 15000);
});
