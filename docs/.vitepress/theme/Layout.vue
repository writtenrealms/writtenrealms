<script setup lang="ts">
import { useData } from "vitepress";
import DefaultTheme from "vitepress/theme";
import VPNavScreenMenuLink from "vitepress/dist/client/theme-default/components/VPNavScreenMenuLink.vue";
import NavExtra from "./NavExtra.vue";

const { theme } = useData();
</script>

<template>
  <DefaultTheme.Layout>
    <template #nav-bar-content-after>
      <NavExtra />
    </template>
    <template #nav-screen-content-after>
      <VPNavScreenMenuLink :item="theme.coreLink" />
    </template>
  </DefaultTheme.Layout>
</template>

<style scoped>
/* Keep one overflow menu at every desktop width. */
:deep(.VPNavBarExtra),
:deep(.VPNavBarAppearance),
:deep(.VPNavBarSocialLinks) {
  display: none;
}
</style>
