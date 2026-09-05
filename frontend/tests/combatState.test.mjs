import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/core/combatState.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { initialCombatState, applyCombatSnapshot, currentCombatTarget } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
);
const snapshot = (overrides = {}) => ({
  encounter_id: 1, state_revision: 1, round: 0, status: 'active', self: 'player.1',
  participants: [
    { key: 'player.1', current_target: 'mob.2', relation: 'self', effects: [] },
    { key: 'mob.2', current_target: 'player.1', relation: 'enemy', effects: [] },
  ], ...overrides,
});

test('full snapshots replace members, targets and effects without retaining removed data', () => {
  const first = applyCombatSnapshot(initialCombatState(), snapshot(), 'player.1');
  assert.equal(currentCombatTarget(first).key, 'mob.2');
  const next = applyCombatSnapshot(first, snapshot({ state_revision: 2, participants: [
    { key: 'player.1', current_target: null, relation: 'self', effects: [] },
  ] }), 'player.1');
  assert.equal(currentCombatTarget(next), null);
  assert.equal(next.current.participants.length, 1);
  assert.equal(first.current.participants.length, 2);
});

test('duplicate and older snapshots cannot roll combat back', () => {
  const state = applyCombatSnapshot(initialCombatState(), snapshot({ state_revision: 7 }), 'player.1');
  assert.equal(applyCombatSnapshot(state, snapshot({ state_revision: 6 }), 'player.1'), state);
  assert.equal(applyCombatSnapshot(state, snapshot({ state_revision: 7 }), 'player.1'), state);
});

test('a merge retires its donor, including later duplicate deliveries', () => {
  let state = applyCombatSnapshot(initialCombatState(), snapshot(), 'player.1');
  state = applyCombatSnapshot(state, snapshot({ state_revision: 2, status: 'finished', self: null,
                                               participants: [], merged_into: 2 }), 'player.1');
  state = applyCombatSnapshot(state, snapshot({ encounter_id: 2 }), 'player.1');
  state = applyCombatSnapshot(state, snapshot({ state_revision: 99 }), 'player.1');
  assert.equal(state.current.encounter_id, 2);
});

test('observer traffic cannot replace the player fight, and leaving clears it', () => {
  let state = applyCombatSnapshot(initialCombatState(), snapshot(), 'player.1');
  state = applyCombatSnapshot(state, snapshot({ encounter_id: 2, self: null }), 'player.1');
  assert.equal(state.current.encounter_id, 1);
  state = applyCombatSnapshot(state, snapshot({ state_revision: 2, self: null }), 'player.1');
  assert.equal(state.current, null);
});

test('many observed fights do not grow retained client state without bound', () => {
  let state = initialCombatState();
  for (let id = 1; id <= 1000; id++) {
    state = applyCombatSnapshot(state, snapshot({ encounter_id: id, self: null, status: 'finished' }), 'player.1');
  }
  assert.equal(Object.keys(state.revisions).length, 64);
  assert.equal(Object.keys(state.retired).length, 64);
});
