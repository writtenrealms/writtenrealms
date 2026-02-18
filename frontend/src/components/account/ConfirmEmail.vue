<template>
  <div class='email-confirmation my-4' v-if="!user.is_confirmed">
    <div class='mt-2' v-if="!user.is_confirmed">Finish confirming your email with a login link.</div>

    <div class='my-3'>
      <button @click.prevent='onClickResend' class='btn-small'>SEND LOGIN LINK</button>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { useStore } from 'vuex';
import { computed } from 'vue';

const store = useStore();
const user = computed(() => store.state.auth.user);

const onClickResend = async () => {
  await store.dispatch("auth/resendemailconfirmation");
}
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.email-confirmation {
  border-top: 1px solid $color-text-half;
  border-bottom: 1px solid $color-text-half;
}
</style>
