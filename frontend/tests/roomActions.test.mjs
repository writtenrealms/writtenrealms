import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/core/roomActions.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { applyRoomActions } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('room actions change from washing to straining without changing the historical look', () => {
  const old = { key: 'room.114', actions: ['craft', 'wash clay'], actions_revision: 10 };
  const busy = applyRoomActions(old, 24, { world_id: 24, room_key: 'room.114', actions: ['craft'], actions_revision: 11 });
  const ready = applyRoomActions(busy, 24, { world_id: 24, room_key: 'room.114', actions: ['craft', 'strain clay'], actions_revision: 12 });
  assert.deepEqual(old.actions, ['craft', 'wash clay']);
  assert.deepEqual(busy.actions, ['craft']);
  assert.deepEqual(ready.actions, ['craft', 'strain clay']);
});

test('old updates and updates for other rooms or worlds cannot replace current actions', () => {
  const room = { key: 'room.115', actions: ['deliver clay'], actions_revision: 20 };
  for (const patch of [{ room_key: 'room.114' }, { world_id: 25 }, { actions_revision: 19 }]) {
    assert.equal(applyRoomActions(room, 24, { room_key: 'room.115', world_id: 24, actions: [], actions_revision: 21, ...patch }), room);
  }
});
