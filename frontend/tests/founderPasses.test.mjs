import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import axios from 'axios';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { default: Passes } = await server.ssrLoadModule('/src/components/account/Subscriptions.vue');
const { passStatus } = await server.ssrLoadModule('/src/core/founderPasses.ts');

const direct = { id: 1, world_name: 'Phalanx', kind: 'direct', claimed_at: null,
  can_claim: true, can_transfer: true, can_invite: false, transfer_email: null };
async function form() {
  let bindings;
  await renderToString(createSSRApp({ setup() {
    bindings = Passes.setup({}, { expose() {} });
    return () => null;
  } }));
  return bindings;
}

test('claims and +1 invitations show explicit confirmation and never submit on cancel', async () => {
  const bindings = await form();
  const sent = [];
  axios.post = async (url, body) => { sent.push({ url, body }); return { data: {} }; };
  axios.get = async () => ({ data: { passes: [{ ...direct, claimed_at: '2026-10-30', can_invite: true }] } });
  await bindings.claimPass(direct);
  assert.equal(sent.length, 0);
  assert.match(bindings.confirmation.value.message, /no longer be transferred/);
  bindings.confirmation.value = null;
  await bindings.confirmAction();
  assert.equal(sent.length, 0);
  await bindings.claimPass(direct);
  await bindings.confirmAction();
  assert.equal(sent[0].url, '/founders/1/claim/');
  bindings.emails.value[1] = 'friend@example.com';
  await bindings.invitePlusOne(direct);
  assert.equal(sent.length, 1);
  assert.match(bindings.confirmation.value.message, /friend@example.com/);
  assert.match(bindings.confirmation.value.message, /cannot be transferred/);
  // Editing the field after review cannot change the confirmed destination.
  bindings.emails.value[1] = 'changed@example.com';
  await bindings.confirmAction();
  assert.equal(sent[1].body.email, 'friend@example.com');
});

test('transfer failures preserve email input and report the server explanation', async () => {
  const bindings = await form();
  bindings.emails.value[1] = 'taken@example.com';
  axios.post = async () => { throw { response: { data: { email: ['This address already has a pass.'] } } }; };
  await bindings.transferPass(direct);
  assert.equal(bindings.error.value, 'This address already has a pass.');
  assert.equal(bindings.emails.value[1], 'taken@example.com');
  assert.equal(bindings.busy.value, false);
  assert.equal(bindings.notice.value, '');
});

test('only pending transfers need a status badge', () => {
  assert.equal(passStatus(direct), '');
  assert.equal(passStatus({ ...direct, transfer_email: 'new@example.com' }), 'Transfer awaiting confirmation');
  assert.equal(passStatus({ ...direct, claimed_at: 'date' }), '');
  assert.equal(passStatus({ ...direct, kind: 'gift', claimed_at: 'date' }), '');
});

test('a failed claim retains its review and never displays a false success', async () => {
  const bindings = await form();
  axios.post = async () => { throw { response: { data: { detail: 'The program is paused.' } } }; };
  bindings.claimPass(direct);
  await bindings.confirmAction();
  assert.equal(bindings.confirmation.value.action, 'claim');
  assert.equal(bindings.error.value, 'The program is paused.');
  assert.equal(bindings.notice.value, '');
});
