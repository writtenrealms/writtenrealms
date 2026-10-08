import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { default: forge } = await server.ssrLoadModule('/src/store/modules/forge.ts');

test('subscription denials display the server permission error', async () => {
  const calls = [];
  await forge.actions.receive({ commit: (...args) => calls.push(args) }, {
    type: 'error', sub: 'staff.panel', error: 'Staff access is required.',
  });
  assert.deepEqual(calls, [['ui/notification_set_error', 'Staff access is required.', { root: true }]]);
});

for (const job of ['toggle_maintenance_mode', 'broadcast']) {
  test(`${job} displays the worker permission error`, async () => {
    const calls = [];
    await forge.actions.job_complete({ commit: (...args) => calls.push(args) }, {
      job, status: 'error', job_data: { error: 'Staff access is required.' },
    });
    assert.deepEqual(calls, [['ui/notification_set_error', 'Staff access is required.', { root: true }]]);
  });
}
