<template>
  <div class="single-form">
    <h1>{{ transfer ? 'Confirm pass transfer' : 'Confirm email change' }}</h1>
    <p v-if="transfer">Confirm to move the offered pass and its unused +1 invitation to the account for the address that received this link. This permanently claims it there. Characters and progress do not move.</p>
    <p v-else>Confirm to use the address that received this link for your existing account. Your characters and passes stay with you.</p>
    <p>You will be signed in to the receiving account after confirmation.</p>
    <p v-if="error" class="inline-notice inline-notice-error" role="alert">{{ error }}</p>
    <p v-if="!token" class="inline-notice">This confirmation link is missing its token. Open the complete link from your email.</p>
    <button v-else class="btn-medium" :disabled="busy" @click="confirm">{{ busy ? 'Confirming…' : 'Confirm' }}</button>
  </div>
</template>

<script setup lang="ts">
import axios from 'axios';
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useStore } from 'vuex';
import { accountActionError } from '@/core/founderPasses';
const route = useRoute();
const router = useRouter();
const store = useStore();
const transfer = route.name === 'founder-transfer-confirm';
const token = new URLSearchParams(route.hash.slice(1)).get('token') || '';
const busy = ref(false);
const error = ref('');
async function confirm() {
  if (busy.value || !token) return;
  busy.value = true; error.value = '';
  try {
    const { data } = await axios.post(transfer ? '/auth/founders/transfer/' : '/auth/email/change/confirm/', { token });
    store.commit('auth/auth_set_tokens', data);
    store.commit('auth/user_set', data.user);
    await router.replace({ name: 'account', hash: transfer ? '#passes' : '#email' });
  } catch (e) { error.value = accountActionError(e); }
  finally { busy.value = false; }
}
</script>
