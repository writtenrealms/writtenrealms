<template>
  <div id="page" class="staff-frame">
    <router-view />
  </div>
</template>
<style scoped lang="scss">
@import '@/styles/layout.scss';
.staff-frame { width: 100%; box-sizing: border-box; max-width: $site-max-width; margin: 0 auto; padding: 32px 20px 64px; min-width: 0; }
@media ($mobile-site) { .staff-frame { padding: 24px 12px 40px; } }
</style>
