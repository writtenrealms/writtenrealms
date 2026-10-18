<script setup lang="ts">
import { useData } from "vitepress";
import VPFlyout from "vitepress/dist/client/theme-default/components/VPFlyout.vue";
import VPMenuLink from "vitepress/dist/client/theme-default/components/VPMenuLink.vue";
import VPSwitchAppearance from "vitepress/dist/client/theme-default/components/VPSwitchAppearance.vue";
import VPSocialLinks from "vitepress/dist/client/theme-default/components/VPSocialLinks.vue";

const { site, theme } = useData();
</script>

<template>
  <VPFlyout class="docs-nav-extra" label="More navigation">
    <div class="group">
      <VPMenuLink :item="theme.coreLink" />
    </div>
    <div v-if="site.appearance && site.appearance !== 'force-dark' && site.appearance !== 'force-auto'" class="group">
      <div class="item appearance">
        <p class="label">{{ theme.darkModeSwitchLabel || "Appearance" }}</p>
        <VPSwitchAppearance />
      </div>
    </div>
    <div v-if="theme.socialLinks" class="group">
      <VPSocialLinks :links="theme.socialLinks" />
    </div>
  </VPFlyout>
</template>

<style scoped>
.docs-nav-extra {
  display: none;
  margin-right: -12px;
}

@media (min-width: 768px) {
  .docs-nav-extra {
    display: block;
  }
}

.appearance {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 176px;
  padding: 0 12px;
}
</style>
