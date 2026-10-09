<template>
  <form class="single-form" @submit.prevent="login">
    <h1>LOG IN</h1>

    <p v-if="admissionMessage" class="inline-notice" role="status">{{ admissionMessage }}</p>

    <template v-if="sent">
      Check your email for a login link.
    </template>

    <template v-else>
      <div class="form-group">
        <label for="field-email">Email</label>
        <input
          id="field-email"
          type="email"
          class="form-control"
          placeholder="Email Address"
          v-model="email"
          name="email"
          :required="true"
          />
      </div>

      <button class="btn-medium">SEND LOGIN LINK</button>
      <p class="auth-switch color-text-60">
        New to Written Realms?
        <router-link :to="{ name: 'signup', query: redirect ? { redirect } : {} }">Sign up</router-link>
      </p>
    </template>

    <template v-if="googleAuthEnabled">
      <!-- 'or' separator -->
      <div class="or-separator">
        <span class="separator-line"></span>
        <span class="separator-text">OR</span>
        <span class="separator-line"></span>
      </div>

      <GoogleLogin :callback="googleLoginCallback" />
    </template>
  </form>
</template>

<script lang='ts' setup>
import { ref, onMounted } from "vue";
import { useStore } from "vuex";
import { useRouter, useRoute } from "vue-router";
import { GoogleLogin } from 'vue3-google-login';
import { GOOGLE_AUTH_ENABLED } from "@/config";
import { rememberPostLoginRedirect, safeRedirect } from "@/core/authRedirect";
import { accessNotice } from "@/core/accessNotice";

const email = ref("");
const sent = ref(false);
const admissionMessage = ref('');
const googleAuthEnabled = GOOGLE_AUTH_ENABLED;

const store = useStore();
const router = useRouter();
const route = useRoute();

onMounted(() => {
  const emailInput = document.getElementById("field-email") as HTMLElement;
  emailInput.focus();
});

const redirect = safeRedirect(route.query.redirect);

const login = async () => {
  admissionMessage.value = '';
  rememberPostLoginRedirect(redirect);
  try {
    await store.dispatch('auth/login', {
      email: email.value
    });
    sent.value = true;
  } catch (e) {
    admissionMessage.value = accessNotice(e);
  }
}

const googleLoginCallback = async (response) => {
  admissionMessage.value = '';
  const result = await store.dispatch('auth/google_login', response.credential);
  if (!result.success) {
    admissionMessage.value = accessNotice(result.error);
    return;
  }
  router.push(redirect || "/lobby");
}
</script>
