// Real signup form, store and response interceptor with synthetic admission
// responses. The adapter handles every request; this preview cannot send mail
// or create accounts. Use ?mode=closed to preview a closed site.
import { createApp } from 'vue';
import axios from 'axios';
import App from '../../src/App.vue';
import store from '../../src/store';
import router from '../../src/router';
import interceptorSetup from '../../src/core/axiosInterceptors';
import '../../src/styles/app.scss';

const closed = new URLSearchParams(location.search).get('mode') === 'closed';
store.state.auth.token = '';
store.state.auth.user = {};
axios.defaults.adapter = async config => {
  if (config.method === 'get' && config.url?.endsWith('/lobby/config/')) {
    return { config, status: 200, statusText: 'OK', headers: {},
      data: { main_world_id: 12, building_enabled: false } };
  }
  if (config.method === 'post' && config.url === '/auth/signup/') {
    throw { response: { status: 403, data: closed ? {
      code: 'site_closed', detail: 'This site is currently closed. Please check back later.',
    } : {
      code: 'invite_only', detail: 'This site is currently invite-only. Please sign in with an invited email address, or contact the administrator to request an invitation.',
    } } };
  }
  throw new Error(`Unexpected preview request: ${config.url}`);
};
interceptorSetup();
// Preserve the harness URL when the real router opens the signup view.
const previewUrl = location.href;
await router.push('/signup?redirect=/worlds/12?create=1');
await router.isReady();
history.replaceState(history.state, '', previewUrl);
createApp(App).use(store).use(router).mount('#preview');
