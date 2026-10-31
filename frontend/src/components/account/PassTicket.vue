<template>
  <div class="pass-ticket">
    <div class="pass-ticket-face">
      <img v-if="artwork && artwork !== failedArtwork" :src="artwork" class="pass-ticket-art"
        alt="" loading="lazy" decoding="async" @error="failedArtwork = artwork" />
      <div class="pass-ticket-seal" aria-hidden="true"><img :src="seal" alt="" width="26" height="28" /></div>
      <div class="pass-ticket-heading">
        <h3 class="pass-ticket-title">{{ title }}</h3>
        <slot name="benefit" />
        <span v-if="status" class="tag">{{ status }}</span>
      </div>
    </div>
    <div class="pass-ticket-stub"><slot /></div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import seal from '@/assets/logo/logo-black.svg';
defineProps<{ title: string; artwork?: string | null; status?: string }>();
const failedArtwork = ref<string | null>();
</script>
