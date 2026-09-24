import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/core/worldEntryMessage.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { initialWorldEntryState, applyWorldEntry, entryMessageParagraphs, startEntryMessage } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
);
const world = (id = 1, overrides = {}) => ({ id, entry_message: 'One.\n\nTwo.\n\nThree.', ...overrides });

test('paragraphs support blank lines, Windows newlines, and literal untrusted text', () => {
  assert.deepEqual(entryMessageParagraphs(' \r\nFirst\r\nline.\r\n \t\r\nSecond.\r\n\r\n'), ['First\nline.', 'Second.']);
  for (const blank of ['', '\n \n', null, undefined]) assert.deepEqual(entryMessageParagraphs(blank), []);
  assert.deepEqual(entryMessageParagraphs('<script>alert(1)</script>'), ['<script>alert(1)</script>']);
});

test('initial entry waits for configuration and defaults to showing all paragraphs', () => {
  const pending = applyWorldEntry(initialWorldEntryState(), { id: 1 });
  assert.equal(pending.message, null);
  const entered = applyWorldEntry(pending, world());
  assert.deepEqual(entered.message, { paragraphs: ['One.', 'Two.', 'Three.'], mode: 'all' });
});

test('state refreshes and reconnect snapshots do not replay a dismissed introduction', () => {
  const dismissed = { ...applyWorldEntry(initialWorldEntryState(), world()), message: null };
  assert.equal(applyWorldEntry(dismissed, world()), dismissed);
  assert.equal(applyWorldEntry(dismissed, world('1')), dismissed);
  assert.equal(applyWorldEntry(dismissed, { name: 'Partial update' }), dismissed);
});

test('transfers, returning to base, and starting a new session show their own introductions', () => {
  let state = applyWorldEntry(initialWorldEntryState(), world());
  state = applyWorldEntry(state, world(2, { entry_message: 'Cave.', entry_message_mode: 'replace' }));
  assert.deepEqual(state.message, { paragraphs: ['Cave.'], mode: 'replace' });
  state = applyWorldEntry({ ...state, message: null }, world());
  assert.equal(state.message.paragraphs[0], 'One.');
  assert.ok(applyWorldEntry(initialWorldEntryState(), world()).message);
});

test('blank and incomplete destinations clear an earlier message without inheriting it', () => {
  const current = applyWorldEntry(initialWorldEntryState(), world());
  const blank = applyWorldEntry(current, world(2, { entry_message: ' \n\n' }));
  assert.equal(blank.message, null);
  assert.equal(blank.worldId, 2);
  const pending = applyWorldEntry(current, { id: 3 });
  assert.equal(pending.message, null);
  assert.ok(applyWorldEntry(pending, world(3)).message);
});

test('all-at-once and single-paragraph modes never advance', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const mode of ['all', 'reveal', 'replace']) {
    const frames = [];
    const paragraphs = mode === 'all' ? ['One', 'Two'] : ['One'];
    const stop = startEntryMessage({ paragraphs, mode }, frame => frames.push(frame));
    t.mock.timers.tick(10000);
    assert.deepEqual(frames, [paragraphs]);
    stop();
  }
});

for (const mode of ['reveal', 'replace']) {
  test(`${mode} advances exactly every three seconds and leaves the last frame visible`, t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const frames = [];
    const stop = startEntryMessage({ paragraphs: ['One', 'Two', 'Three'], mode }, frame => frames.push(frame));
    assert.deepEqual(frames, [['One']]);
    t.mock.timers.tick(2999);
    assert.equal(frames.length, 1);
    t.mock.timers.tick(1);
    assert.deepEqual(frames.at(-1), mode === 'reveal' ? ['One', 'Two'] : ['Two']);
    t.mock.timers.tick(3000);
    assert.deepEqual(frames.at(-1), mode === 'reveal' ? ['One', 'Two', 'Three'] : ['Three']);
    t.mock.timers.tick(30000);
    assert.equal(frames.length, 3);
    stop();
  });

  test(`${mode} cancels pending paragraphs when dismissed or unmounted`, t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const frames = [];
    const stop = startEntryMessage({ paragraphs: ['One', 'Two'], mode }, frame => frames.push(frame));
    stop();
    t.mock.timers.tick(30000);
    assert.deepEqual(frames, [['One']]);
  });
}
