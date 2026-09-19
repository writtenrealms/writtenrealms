import { createApp } from 'vue'
import './styles/app.scss'
import App from './App.vue'
import router from './router'
import store from './store'
import interceptorSetup from '@/core/axiosInterceptors'
import vue3GoogleLogin from 'vue3-google-login'
import { GOOGLE_AUTH_ENABLED, GOOGLE_CLIENT_ID } from '@/config'
import { interactive } from '@/core/directives'
import FloatingVue from 'floating-vue';
import 'floating-vue/dist/style.css';
import '@/styles/floating-vue-custom.scss';

interceptorSetup();

const app = createApp(App)
  .use(router)
  .use(store);

let pageResourcesInitialized = false;
const initializePageResources = (path: string) => {
  // Callback parameters must be removed before loading third-party scripts.
  if (pageResourcesInitialized || path.startsWith('/auth/wr1/')) return;
  pageResourcesInitialized = true;
  const font = document.createElement('link');
  font.rel = 'stylesheet';
  font.href = 'https://fonts.googleapis.com/css?family=Roboto+Slab:400,300,100|Roboto+Mono';
  document.head.appendChild(font);
  const typekit = document.createElement('script');
  typekit.src = 'https://use.typekit.net/zcm6whn.js';
  typekit.onload = () => { try { (window as any).Typekit.load({ async: true }); } catch (e) {} };
  document.head.appendChild(typekit);
  const tween = document.createElement('script');
  tween.src = 'https://cdnjs.cloudflare.com/ajax/libs/gsap/2.1.3/TweenLite.min.js';
  document.head.appendChild(tween);
  if (GOOGLE_AUTH_ENABLED) {
    app.use(vue3GoogleLogin, { clientId: GOOGLE_CLIENT_ID });
  }
};
initializePageResources(window.location.pathname);
router.afterEach(to => initializePageResources(to.path));

app
  .use(FloatingVue)
  .directive('interactive', interactive)
  .mount('#app')
