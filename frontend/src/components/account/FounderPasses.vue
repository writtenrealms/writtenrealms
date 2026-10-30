<template>
  <section id="passes" class="account-section" aria-labelledby="passes-title">
    <h2 id="passes-title">Founder’s Passes</h2>
    <p v-if="error" class="inline-notice inline-notice-error" role="alert">{{ error }}</p>
    <button v-if="error && !passes.length" class="btn-small button-gray" :disabled="loading" @click="load">Try again</button>
    <p v-if="notice" class="inline-notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="account-note" role="status">Loading your passes…</p>
    <p v-else-if="!error && !passes.length" class="account-note">You don’t have a Founder’s Pass yet. If you received an invitation, sign in with the email address it was sent to.</p>
    <article v-for="pass in passes" :key="pass.id" class="pass-entry" :aria-label="`${pass.world_name} Founder’s Pass`">
      <h3>{{ pass.world_name }}</h3>
      <span class="tag" :class="{ 'tag-green': pass.claimed_at }">{{ passStatus(pass) }}</span>
      <p>Complimentary access, for as long as this world operates.</p>
      <template v-if="pass.can_claim">
        <p>Claim this pass to keep it on your account and unlock your +1 invitation.</p>
        <p class="account-note" v-if="pass.claim_deadline">Automatically claimed on {{ date(pass.claim_deadline) }}.</p>
        <p class="account-note" v-else>Automatically claimed at the end of beta. No date has been set yet.</p>
        <button v-if="confirmation?.pass.id !== pass.id" class="btn-medium" :disabled="busy" @click="claimPass(pass)">Claim on this account</button>
      </template>
      <p v-else class="account-note">Claimed on your account. You can still <router-link :to="{ name: 'account', hash: '#email' }">change your email</router-link> and keep your pass and characters.</p>
      <p v-if="pass.kind === 'gift'" class="account-note">This gifted pass cannot be transferred and does not include a +1 invitation.</p>
      <p v-if="pass.invited_email" class="account-note">Your +1 pass was granted to <strong>{{ pass.invited_email }}</strong>. It does not include another invitation.</p>
      <template v-if="pass.transfer_email">
        <p class="inline-notice">Waiting for {{ pass.transfer_email }} to confirm by {{ date(pass.transfer_expires_at!) }}. Until then, the pass remains yours.</p>
        <button class="btn-small button-gray" :disabled="busy" @click="act(pass, 'cancel-transfer', {}, 'Transfer canceled.')">Cancel transfer</button>
      </template>
      <form v-if="pass.can_invite && confirmation?.pass.id !== pass.id" class="pass-invite" @submit.prevent="invitePlusOne(pass)">
        <h4>Your +1 invitation</h4>
        <p class="account-note">Give someone a Founder’s Pass for {{ pass.world_name }}. They cannot transfer it or invite another person. Your unused invitation stays available after beta.</p>
        <div class="form-group"><label :for="`invite-${pass.id}`">Your +1’s email</label><input :id="`invite-${pass.id}`" v-model="emails[pass.id]" type="email" required :disabled="busy" /></div>
        <button class="btn-medium" :disabled="busy">Invite your +1</button>
      </form>
      <div v-if="confirmation?.pass.id === pass.id" class="inline-notice" role="group" aria-label="Confirm pass action">
        <p>{{ confirmation.message }}</p>
        <div class="account-actions">
          <button class="btn-small" :disabled="busy" @click="confirmAction">{{ confirmation.action === 'claim' ? 'Confirm claim' : 'Send +1 invitation' }}</button>
          <button class="btn-small button-gray" :disabled="busy" @click="confirmation = null">Cancel</button>
        </div>
      </div>
      <details v-if="pass.can_transfer && !pass.transfer_email && confirmation?.pass.id !== pass.id" class="account-disclosure">
        <summary><ChevronRight :size="16" aria-hidden="true" />Transfer to another account</summary>
        <form @submit.prevent="transferPass(pass)">
          <p class="account-note">Transfer once, before claiming. The recipient confirms by email, which claims the pass and its unused +1 on their account. Your characters and progress stay here.</p>
          <p class="account-note">Just changing your address? <router-link :to="{ name: 'account', hash: '#email' }">Change your account email</router-link> instead.</p>
          <div class="form-group"><label :for="`transfer-${pass.id}`">Destination email</label><input :id="`transfer-${pass.id}`" v-model="emails[pass.id]" type="email" required :disabled="busy" /></div>
          <button class="btn-small button-gray" :disabled="busy">Send transfer confirmation</button>
        </form>
      </details>
      <p class="account-note pass-terms">Normal conduct rules and usage limits apply.</p>
      <router-link class="account-link" :to="{ name: 'lobby_world_details', params: { world_id: pass.world_id } }">Visit {{ pass.world_name }} <ArrowRight :size="16" aria-hidden="true" /></router-link>
    </article>
  </section>
</template>

<script setup lang="ts">
import axios from 'axios';
import { onMounted, ref } from 'vue';
import { ArrowRight, ChevronRight } from '@lucide/vue';
import { accountActionError, passStatus, type FounderPass } from '@/core/founderPasses';
const emit = defineEmits(['loaded']);
const passes = ref<FounderPass[]>([]);
const emails = ref<Record<number, string>>({});
const loading = ref(true);
const busy = ref(false);
const error = ref('');
const notice = ref('');
const confirmation = ref<{ pass: FounderPass; action: 'claim' | 'invite'; email?: string; message: string } | null>(null);
const date = (value: string) => new Date(value).toLocaleString();
async function load() {
  loading.value = true; error.value = '';
  try { passes.value = (await axios.get('/founders/')).data.passes; }
  catch (e) { error.value = accountActionError(e); }
  finally { loading.value = false; }
}
async function act(pass: FounderPass, action: string, data: object, message: string) {
  if (busy.value) return;
  busy.value = true; error.value = ''; notice.value = '';
  try {
    await axios.post(`/founders/${pass.id}/${action}/`, data);
    notice.value = message;
    emails.value[pass.id] = '';
    await load();
    return true;
  } catch (e) { error.value = accountActionError(e); return false; }
  finally { busy.value = false; }
}
function claimPass(pass: FounderPass) {
  confirmation.value = { pass, action: 'claim', message: `Claim your ${pass.world_name} pass on this account. It can no longer be transferred, and your +1 invitation becomes available.` };
}
function transferPass(pass: FounderPass) {
  return act(pass, 'transfer', { email: emails.value[pass.id] }, 'Transfer confirmation queued. Your pass stays here until the recipient confirms.');
}
function invitePlusOne(pass: FounderPass) {
  const email = emails.value[pass.id]?.trim();
  confirmation.value = { pass, action: 'invite', email, message: `Give your one +1 pass to ${email}. It will be claimed on that account and cannot be transferred.` };
}
async function confirmAction() {
  const pending = confirmation.value;
  if (!pending || busy.value) return;
  const succeeded = await act(pending.pass, pending.action, pending.action === 'invite' ? { email: pending.email } : {},
    pending.action === 'claim' ? 'Your pass is claimed. You can now invite your +1.' : 'Your +1 pass has been granted and its email notification queued.');
  if (succeeded) confirmation.value = null;
}
onMounted(async () => { await load(); emit('loaded'); });
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';
@import '@/styles/fonts.scss';
.pass-entry {
  padding-bottom: 28px;
  border-bottom: 1px solid $color-background-border;
  & + & { padding-top: 28px; }
  h3 { @include font-title-regular; color: $color-text; opacity: 1; font-size: 28px; margin: 0 0 10px; }
  h4 { @include font-title-regular; font-size: 17px; margin: 0 0 12px; }
  .pass-invite { border-top: 1px solid $color-background-border; margin-top: 24px; padding-top: 24px; }
  .pass-terms { margin: 24px 0 8px; font-size: 12px; }
}
</style>
