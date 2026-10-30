<template>
  <div class="account-email">
    <div class="account-label">Email address</div>
    <p class="email-address">{{ store.state.auth.user.email }}</p>
    <details id="email" class="account-disclosure" :open="route.hash === '#email'">
      <summary><ChevronRight :size="16" aria-hidden="true" />Change email address</summary>
      <form @submit.prevent="submit">
    <p>Your characters, progress, and passes stay with this account. We’ll send a confirmation link to your new address.</p>
    <p class="account-note">After confirming, use the new email to sign in. Previous linked sign-ins will be disconnected.</p>
    <p v-if="error" class="inline-notice inline-notice-error" role="alert">{{ error }}</p>
    <p v-if="sentTo" class="inline-notice" role="status">Confirmation queued for {{ sentTo }}. Your current address remains active until you confirm.</p>
    <div class="form-group"><label for="new-email">New email address</label><input id="new-email" v-model="email" class="form-control" type="email" required :disabled="busy" /></div>
    <button class="btn-small button-gray" :disabled="busy">{{ busy ? 'Sending…' : 'Send confirmation' }}</button>
      </form>
    </details>
  </div>
</template>

<script setup lang="ts">
import axios from 'axios';
import { ref } from 'vue';
import { useStore } from 'vuex';
import { useRoute } from 'vue-router';
import { ChevronRight } from '@lucide/vue';
import { accountActionError } from '@/core/founderPasses';
const store = useStore();
const route = useRoute();
const email = ref('');
const busy = ref(false);
const sentTo = ref('');
const error = ref('');
async function submit() {
  if (busy.value) return;
  const destination = email.value.trim();
  busy.value = true; error.value = ''; sentTo.value = '';
  try { await axios.post('/auth/email/change/', { email: destination }); sentTo.value = destination; }
  catch (e) { error.value = accountActionError(e); }
  finally { busy.value = false; }
}
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';
.account-email { margin-bottom: 28px; }
.email-address { margin: 4px 0; overflow-wrap: anywhere; }
.account-disclosure { margin: 0; border: 0; padding: 0; }
</style>
