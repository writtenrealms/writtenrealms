<template>
  <div class="single-form">
    <h1>SIGN IN FROM ALPHA</h1>
    <p v-if="busy">Completing your sign-in...</p>
    <template v-else-if="error">
      <p role="alert">{{ error }}</p>
      <button v-if="target" class="btn-medium" @click="error = ''; busy = false">TRY AGAIN</button>
      <p><router-link to="/login">Use Core email login</router-link></p>
      <p>To restart the handoff, return to Alpha and open the Core world again.</p>
    </template>
    <template v-else-if="target">
      <p v-if="switchNeeded">You are signed into a different Core account. Continuing will switch this browser to {{ target.email }}.</p>
      <template v-if="target.status === 'verification_required'">
        <p>Verify {{ target.email }} once to connect your Alpha identity to Core.</p>
        <button class="btn-medium" @click="sendCode" :disabled="sending">
          {{ sending ? 'SENDING...' : (codeSent ? 'SEND ANOTHER CODE' : 'EMAIL ME A CODE') }}
        </button>
        <form v-if="codeSent" @submit.prevent="finish">
          <p>Check your email, then enter the eight-digit code here.</p>
          <label for="wr1-code">Verification code</label>
          <input id="wr1-code" class="form-control" v-model="proof" inputmode="numeric"
            autocomplete="one-time-code" pattern="[0-9]{8}" maxlength="8" required />
          <button class="btn-medium">{{ switchNeeded ? 'VERIFY AND SWITCH ACCOUNT' : 'VERIFY AND CONTINUE' }}</button>
        </form>
      </template>
      <button v-else class="btn-medium" @click="finish">{{ switchNeeded ? 'SWITCH ACCOUNT AND CONTINUE' : 'CONTINUE' }}</button>
    </template>
  </div>
</template>

<script lang="ts" setup>
import axios from 'axios';
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useStore } from 'vuex';
import { callbackParameters, needsAccountSwitch, worldDestination } from '@/core/wr1SignIn';

// Same-origin requests carry the HttpOnly browser binding. No Alpha or ambient
// Core JWT is sent to these endpoints, and the bridge secret stays server-side.
const api = axios.create({ baseURL: '/api/v1/auth/wr1/' });
const route = useRoute();
const router = useRouter();
const store = useStore();
const busy = ref(true);
const sending = ref(false);
const codeSent = ref(false);
const proof = ref('');
const error = ref('');
const state = ref('');
const target = ref<{ status: string; email: string; user_id: number | null; destination: string } | null>(null);
const storageKey = 'wr1-sign-in-state';
const switchNeeded = computed(() => target.value && needsAccountSwitch(
  store.state.auth.user, !!store.state.auth.token, target.value.user_id));

function showError(failure: any) {
  error.value = failure.response?.data?.detail || failure.message || 'Sign-in could not be completed. Please start again.';
  busy.value = false;
}

async function finish() {
  busy.value = true;
  try {
    const { data } = await api.post('finish/', { state: state.value, ...(proof.value ? { proof: proof.value } : {}) });
    if (data.status !== 'authenticated') {
      target.value = data;
      busy.value = false;
      return;
    }
    const destination = worldDestination(data.destination);
    store.commit('auth/auth_set_tokens', { access: data.access, refresh: data.refresh });
    store.commit('auth/user_set', data.user);
    sessionStorage.removeItem(storageKey);
    await router.replace(destination);
  } catch (failure) { showError(failure); }
}

async function sendCode() {
  sending.value = true;
  try {
    await api.post('email/', { state: state.value });
    codeSent.value = true;
  } catch (failure) { showError(failure); }
  finally { sending.value = false; }
}

onMounted(async () => {
  try {
    if (route.name === 'wr1-start') {
      if (typeof route.query.world !== 'string' || !/^[1-9][0-9]*$/.test(route.query.world)) {
        throw new Error('Choose a Core world from Alpha to start signing in.');
      }
      sessionStorage.removeItem(storageKey);
      const { data } = await api.post('start/', { world: route.query.world });
      const url = new URL(data.authorization_url);
      if (!['https:', 'http:'].includes(url.protocol)) throw new Error('Invalid Alpha destination.');
      window.location.replace(url.href);
      return;
    }
    let data;
    if (route.query.code !== undefined || route.query.state !== undefined) {
      const params = callbackParameters(route.query);
      state.value = params.state;
      sessionStorage.setItem(storageKey, state.value);
      // Remove the one-use code before any further navigation or API request.
      await router.replace({ name: 'wr1-callback' });
      ({ data } = await api.post('callback/', params));
    } else {
      state.value = sessionStorage.getItem(storageKey) || '';
      ({ data } = await api.post('status/', { state: state.value }));
    }
    target.value = data;
    busy.value = false;
    if (data.status === 'ready' && !switchNeeded.value) await finish();
  } catch (failure) { showError(failure); }
});
</script>
