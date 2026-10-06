import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/core/consoleScroll.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { createConsoleScrollFollower } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`,
);

const fixture = () => {
  let top = 0;
  const viewport = {
    scrollHeight: 1000,
    clientHeight: 400,
    get scrollTop() { return Math.max(0, Math.min(top, this.scrollHeight - this.clientHeight)); },
    set scrollTop(value) { top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
  };
  const distances = [];
  const follower = createConsoleScrollFollower(viewport, distance => distances.push(distance));
  follower.scrollToBottom();
  return { viewport, follower, distances };
};

test('a delayed room action does not turn a previously pinned console into scrollback', () => {
  const { viewport, follower, distances } = fixture();
  viewport.scrollHeight += 32;
  // A queued native scroll event may arrive before the resize observer.
  follower.onScroll();
  follower.onLayoutChange();
  follower.onScroll();
  assert.equal(viewport.scrollTop, 632);
  assert.deepEqual(distances, [0]);
});

test('late layout growth follows without a new message or a scroll event', () => {
  const { viewport, follower, distances } = fixture();
  viewport.scrollHeight += 220;
  follower.onLayoutChange();
  assert.equal(viewport.scrollTop, 820);
  assert.deepEqual(distances, [0]);
});

test('duplicate queued scroll events after growth do not abandon bottom following', () => {
  const { viewport, follower, distances } = fixture();
  viewport.scrollHeight += 220;
  follower.onScroll();
  follower.onScroll();
  follower.onLayoutChange();
  assert.equal(viewport.scrollTop, 820);
  assert.deepEqual(distances, [0]);
});

test('pruning old messages while adding replies keeps following through native clamping', () => {
  const { viewport, follower, distances } = fixture();
  // At the history limit, replacing a tall room with a short command shrinks
  // the transcript, then the reply may grow it. Native anchoring is disabled
  // while following, so the browser only clamps to the new maximum.
  for (const delta of [-84, -65, 220, 0]) {
    viewport.scrollHeight += delta;
    follower.onScroll();
    follower.onScroll();
    follower.onLayoutChange();
    assert.equal(viewport.scrollTop, viewport.scrollHeight - viewport.clientHeight);
  }
  assert.deepEqual(distances, [0]);
});

test('scrolling up before a pending follow preserves the readers position', () => {
  const { viewport, follower, distances } = fixture();
  viewport.scrollTop -= 120;
  follower.onScroll();
  viewport.scrollHeight += 100;
  follower.onLayoutChange();
  assert.equal(viewport.scrollTop, 480);
  assert.equal(distances.at(-1), 220);
  viewport.clientHeight -= 80;
  follower.onLayoutChange();
  assert.equal(viewport.scrollTop, 480);
  assert.equal(distances.at(-1), 300);
});

test('upward scrolling still disengages following when content grows concurrently', () => {
  const { viewport, follower, distances } = fixture();
  viewport.scrollHeight += 32;
  viewport.scrollTop -= 80;
  follower.onScroll();
  follower.onLayoutChange();
  assert.equal(viewport.scrollTop, 520);
  assert.equal(distances.at(-1), 112);
});

test('resizing and collapsing content preserve bottom following despite native clamping', () => {
  const { viewport, follower, distances } = fixture();
  for (const [height, visible] of [[1000, 550], [800, 550], [800, 300], [200, 300], [900, 300]]) {
    viewport.scrollHeight = height;
    viewport.clientHeight = visible;
    follower.onScroll();
    follower.onLayoutChange();
    assert.equal(viewport.scrollTop, Math.max(0, height - visible));
    assert.equal(distances.at(-1), 0);
  }
});

test('jumping or manually returning to the bottom resumes following', () => {
  for (const jump of [false, true]) {
    const { viewport, follower, distances } = fixture();
    viewport.scrollTop = 100;
    follower.onScroll();
    if (jump) follower.scrollToBottom();
    else {
      viewport.scrollTop = 600;
      follower.onScroll();
    }
    viewport.scrollHeight += 32;
    follower.onLayoutChange();
    assert.equal(viewport.scrollTop, 632);
    assert.equal(distances.at(-1), 0);
  }
});

test('fractional near-bottom offsets do not show a spurious jump control', () => {
  const { viewport, follower, distances } = fixture();
  viewport.scrollTop -= 0.5;
  follower.onScroll();
  viewport.scrollHeight += 32;
  follower.onLayoutChange();
  assert.equal(viewport.scrollTop, 632);
  assert.deepEqual(distances, [0]);
});
