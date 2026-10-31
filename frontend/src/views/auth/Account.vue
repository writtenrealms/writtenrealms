<template>
  <main class="account-page">
    <header class="account-title">
      <h1>Account</h1>
      <p>Your profile, preferences, and world access.</p>
    </header>
    <div class="account-columns">
      <section class="account-section" aria-labelledby="profile-title">
        <h2 id="profile-title">Profile</h2>
        <EmailSettings />
        <ConfirmEmail />
        <form @submit.prevent="save">
          <fieldset :disabled="saving">
            <FormField v-model="draft.name" :element-schema="{ attr: 'name', label: 'Username' }" :form-errors="fieldErrors" />
            <div class="account-preferences">
              <h2>Preferences</h2>
              <FormField v-model="draft.send_newsletter" :element-schema="{ attr: 'send_newsletter', label: 'Receive news about Written Realms', widget: 'checkbox' }" :form-errors="fieldErrors" />
              <p class="account-note">Infrequent updates about new features, less than once a month.</p>
              <FormField v-model="draft.accessibility_mode" :element-schema="{ attr: 'accessibility_mode', label: 'Use the accessibility layout', widget: 'checkbox' }" :form-errors="fieldErrors" />
            </div>
            <p v-if="saveError" class="inline-notice inline-notice-error" role="alert">{{ saveError }}</p>
            <div class="account-actions">
              <button class="btn-medium button-gray" :disabled="!dirty">{{ saving ? 'Saving…' : 'Save changes' }}</button>
              <span v-if="saved && !dirty" class="account-note" role="status">Changes saved.</span>
            </div>
          </fieldset>
        </form>
      </section>
      <Subscriptions @loaded="showSection" />
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useStore } from 'vuex';
import { useRoute } from 'vue-router';
import FormField from '@/components/forms/FormField.vue';
import ConfirmEmail from '@/components/account/ConfirmEmail.vue';
import EmailSettings from '@/components/account/EmailSettings.vue';
import Subscriptions from '@/components/account/Subscriptions.vue';
import { accountActionError } from '@/core/founderPasses';

const store = useStore();
const route = useRoute();
const profile = () => ({ name: store.state.auth.user.name || '',
  send_newsletter: !!store.state.auth.user.send_newsletter,
  accessibility_mode: !!store.state.auth.user.accessibility_mode });
const draft = reactive(profile());
const baseline = ref(profile());
const dirty = computed(() => JSON.stringify(draft) !== JSON.stringify(baseline.value));
const saving = ref(false);
const saved = ref(false);
const saveError = ref('');
const fieldErrors = ref({});

async function save() {
  if (saving.value || !dirty.value) return;
  saving.value = true; saved.value = false; saveError.value = ''; fieldErrors.value = {};
  try {
    await store.dispatch('auth/account_save', { ...draft });
    Object.assign(draft, profile());
    baseline.value = profile();
    saved.value = true;
  } catch (error: any) {
    const data = error?.response?.data;
    fieldErrors.value = data && typeof data === 'object' && !Array.isArray(data) ? data : {};
    saveError.value = error?.response?.data?.name?.[0] || accountActionError(error);
  } finally { saving.value = false; }
}

async function showSection() {
  await nextTick();
  const id = route.hash.slice(1);
  if (['email', 'passes'].includes(id)) document.getElementById(id)?.scrollIntoView({ block: 'start' });
}
watch(() => route.hash, showSection);
onMounted(showSection);
</script>

<style scoped lang="scss">
@import '@/styles/layout.scss';
@import '@/styles/colors.scss';
@import '@/styles/fonts.scss';
.account-page {
  box-sizing: border-box;
  max-width: $site-max-width;
  margin: 0 auto;
  padding: 64px 30px 80px 80px;
  .account-title {
    margin-bottom: 52px;
    h1 { @include font-title-regular; font-size: 56px; line-height: 1.1; letter-spacing: 4px; text-transform: uppercase; margin: 0 0 16px; }
    p { @include font-text-light; color: $color-text-70; font-size: 16px; line-height: 1.75; margin: 0; }
  }
  .account-columns { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(280px, 1fr); gap: 64px; align-items: start; }
  fieldset { padding: 0; margin: 0; border: 0; min-width: 0; }
  .account-preferences { margin: 32px 0; padding-top: 28px; border-top: 1px solid $color-background-border; }
  .account-preferences .account-note { margin: -4px 0 24px; }
  @media (max-width: 850px) { .account-columns { grid-template-columns: minmax(0, 1fr); gap: 48px; } }
  @media ($mobile-site) {
    padding: 36px 20px 60px;
    .account-title { margin-bottom: 36px; h1 { font-size: 38px; letter-spacing: 2.5px; } }
  }
}
</style>
