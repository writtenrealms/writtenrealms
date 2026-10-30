// Real account page/navigation with synthetic data and an entirely in-memory
// adapter. This harness cannot change accounts, issue passes, or send email.
import { createApp } from 'vue';
import axios from 'axios';
import App from '../../src/App.vue';
import store from '../../src/store';
import router from '../../src/router';
import interceptorSetup from '../../src/core/axiosInterceptors';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
const mode = params.get('mode') || 'unclaimed';
let user = { id: 1, name: 'Alice', email: params.has('long') ? 'a-very-long-but-valid-account-address-for-mobile-testing@example.com' : 'alice@example.com',
  is_confirmed: true, send_newsletter: false, accessibility_mode: false, is_staff: false };
store.state.auth.token = 'synthetic-preview';
store.state.auth.user = user;
const claimed = ['claimed', 'gift', 'invited'].includes(mode);
let pass = { id: 1, world_id: 12, world_name: 'Phalanx', kind: mode === 'gift' ? 'gift' : 'direct',
  claimed_at: claimed ? '2026-10-30T12:00:00Z' : null, claim_reason: '', claim_deadline: null,
  can_claim: !claimed, can_transfer: !claimed, can_invite: mode === 'claimed',
  invited_email: mode === 'invited' ? 'friend@example.com' : null,
  transfer_email: mode === 'pending' ? 'recipient@example.com' : null, transfer_expires_at: '2026-10-31T12:00:00Z' };
axios.defaults.adapter = async config => {
  let data: any = {};
  const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
  if (config.url?.endsWith('/lobby/config/')) data = { main_world_id: 12, building_enabled: false, founders_enabled: mode !== 'disabled' };
  else if (config.url === '/founders/') data = { passes: mode === 'empty' ? [] : [pass] };
  else if (config.url === '/user/' && config.method === 'put') { user = { ...user, ...body }; data = user; }
  else if (config.url === '/founders/1/claim/') { pass = { ...pass, claimed_at: '2026-10-30', can_claim: false, can_transfer: false, can_invite: true }; }
  else if (config.url === '/founders/1/invite/') { pass = { ...pass, invited_email: body.email, can_invite: false }; }
  else if (config.url === '/founders/1/transfer/') { pass = { ...pass, transfer_email: body.email }; }
  else if (config.url === '/founders/1/cancel-transfer/') { pass = { ...pass, transfer_email: null }; }
  else if (config.url !== '/auth/email/change/') throw new Error(`Unexpected preview request: ${config.url}`);
  return { config, status: 200, statusText: 'OK', headers: {}, data };
};
interceptorSetup();
const previewUrl = location.href;
await router.push(params.get('route') || '/account');
await router.isReady();
history.replaceState(history.state, '', previewUrl);
createApp(App).use(store).use(router).mount('#preview');
