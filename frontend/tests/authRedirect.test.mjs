import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { safeRedirect, rememberPostLoginRedirect, takePostLoginRedirect } =
  await server.ssrLoadModule('/src/core/authRedirect.ts');

const storage = new Map();
globalThis.localStorage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
};

test('only paths on this site are accepted as destinations', () => {
  assert.equal(safeRedirect('/worlds/12?create=1'), '/worlds/12?create=1');
  for (const value of ['https://evil.example', '//evil.example', '/\\evil.example', 'worlds/12', '', null, ['/a']]) {
    assert.equal(safeRedirect(value), null, String(value));
  }
});

test('a remembered destination is used once after the login link', () => {
  rememberPostLoginRedirect('/worlds/12?create=1');
  assert.equal(takePostLoginRedirect(), '/worlds/12?create=1');
  assert.equal(takePostLoginRedirect(), null);
  rememberPostLoginRedirect('/worlds/12');
  rememberPostLoginRedirect('https://evil.example');
  assert.equal(takePostLoginRedirect(), null);
});
