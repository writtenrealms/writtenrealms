<template>
  <div class="single-form">
    <h1>LOGGING IN</h1>

    <template v-if="status === 'loading'">
      Verifying your login link...
    </template>
    <template v-else-if="status === 'success'">
      Success! Redirecting...
    </template>
    <template v-else>
      <p class="inline-notice inline-notice-error" role="alert">{{ errorMessage }}</p>
      <button v-if="retryable" class="btn-medium" @click="confirmLogin">Try again</button>
      <p class="my-3"><router-link :to="{ name: 'login' }">Request a new login link</router-link></p>
    </template>
  </div>
</template>

<script lang='ts' setup>
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import { takePostLoginRedirect } from "@/core/authRedirect";
import { loginLinkError } from '@/core/loginLinkError';

const store = useStore();
const route = useRoute();
const router = useRouter();

const status = ref("loading");
const errorMessage = ref('');
const retryable = ref(false);

async function confirmLogin() {
  status.value = 'loading';
  errorMessage.value = '';
  retryable.value = false;
  try {
    const token = Array.isArray(route.params.token)
      ? route.params.token[0]
      : route.params.token;
    if (!token) {
      errorMessage.value = 'This login link is missing its token. Please request a new one.';
      status.value = "error";
      return;
    }
    await store.dispatch("auth/confirm_login_link", token);
    status.value = "success";
    router.push(takePostLoginRedirect() || { name: "lobby" });
  } catch (e: any) {
    const failure = loginLinkError(e);
    errorMessage.value = failure.message;
    retryable.value = failure.retryable;
    status.value = "error";
  }
}

onMounted(confirmLogin);
</script>

<style lang="scss" scoped>
@import "@/styles/layout.scss";
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
</style>
