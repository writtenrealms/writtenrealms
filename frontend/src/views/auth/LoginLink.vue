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
      This login link is invalid or expired.
    </template>
  </div>
</template>

<script lang='ts' setup>
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";

const store = useStore();
const route = useRoute();
const router = useRouter();

const status = ref("loading");

onMounted(async () => {
  try {
    const token = Array.isArray(route.params.token)
      ? route.params.token[0]
      : route.params.token;
    if (!token) {
      status.value = "error";
      return;
    }
    await store.dispatch("auth/confirm_login_link", token);
    status.value = "success";
    router.push({ name: "lobby" });
  } catch (e: any) {
    status.value = "error";
  }
});
</script>

<style lang="scss" scoped>
@import "@/styles/layout.scss";
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
</style>
