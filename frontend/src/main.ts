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

if (GOOGLE_AUTH_ENABLED) {
  app.use(vue3GoogleLogin, {
    clientId: GOOGLE_CLIENT_ID,
  });
}

app
  .use(FloatingVue)
  .directive('interactive', interactive)
  .mount('#app')
