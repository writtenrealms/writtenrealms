import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
after(() => server.close());
const { callbackParameters, needsAccountSwitch, worldDestination } = await server.ssrLoadModule('/src/core/wr1SignIn.ts');

test('callback requires exactly one opaque code and state', () => {
  const params = { code: 'c'.repeat(43), state: 's'.repeat(43) };
  assert.deepEqual(callbackParameters(params), params);
  for (const changed of [{ code: [] }, { state: ['s'.repeat(43)] }, { state: '' }, { code: 'short' }]) {
    assert.throws(() => callbackParameters({ ...params, ...changed }));
  }
});

test('an ambient different or unidentified account requires an explicit switch', () => {
  assert.equal(needsAccountSwitch({}, false, 10), false);
  assert.equal(needsAccountSwitch({ id: 10 }, true, 10), false);
  assert.equal(needsAccountSwitch({ id: 10 }, true, 11), true);
  assert.equal(needsAccountSwitch({ id: 10 }, true, null), true);
  assert.equal(needsAccountSwitch({}, true, 10), true);
});

test('only Core world destinations can receive credentials', () => {
  assert.equal(worldDestination('/worlds/42'), '/worlds/42');
  for (const value of ['//evil.test', 'https://evil.test', '/worlds/42?next=evil', '/worlds/../login', null]) {
    assert.throws(() => worldDestination(value));
  }
});
