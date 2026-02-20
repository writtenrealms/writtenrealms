<template>
  <form class="single-form" @submit.prevent="login">
    <h1>LOG IN</h1>

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

const email = ref("");
const sent = ref(false);
const googleAuthEnabled = GOOGLE_AUTH_ENABLED;

const store = useStore();
const router = useRouter();
const route = useRoute();

onMounted(() => {
  const emailInput = document.getElementById("field-email") as HTMLElement;
  emailInput.focus();
});

const login = async () => {
  await store.dispatch('auth/login', {
    email: email.value
  });
  sent.value = true;
}

const googleLoginCallback = async (response) => {
  await store.dispatch('auth/google_login', response.credential);

  // Check if there's a redirect query parameter
  if (route.query.redirect) {
    router.push(route.query.redirect as string);
  } else {
    router.push("/lobby");
  }
}
</script>
