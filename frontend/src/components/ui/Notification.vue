<template>
  <div id="notification-box">
    <div class="ui-notification" :class="[notificationType, { multiline: isMultiline }]">
      <LoaderCircle v-if="notificationType === 'loading'" class="notification-icon notification-spinner" aria-hidden="true" />
      <CircleAlert v-else-if="notificationType === 'error'" class="notification-icon" aria-hidden="true" />
      <Check v-else class="notification-icon" aria-hidden="true" />
      <div class="message" :role="notificationType === 'error' ? 'alert' : 'status'" aria-atomic="true">{{ notification }}</div>
      <button type="button" class="icon-button notification-close" aria-label="Dismiss notification" @click="closeNotification">
        <X :size="16" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, watch } from "vue";
import { useStore } from "vuex";
import { Check, CircleAlert, LoaderCircle, X } from "@lucide/vue";

const store = useStore();
let timeout: ReturnType<typeof setTimeout> | null = null;

const notificationType = computed(() => store.state.ui.notificationType);
const notification = computed(() => String(store.state.ui.notification ?? ""));
const isMultiline = computed(() => notification.value.includes("\n"));

const clearExpiry = () => {
  if (timeout) {
    clearTimeout(timeout);
    timeout = null;
  }
};

const restartExpiry = () => {
  clearExpiry();
  if (notification.value && store.state.ui.notification_expires) {
    timeout = setTimeout(() => {
      store.commit("ui/notification_clear");
    }, store.state.ui.notification_expires);
  }
};

onMounted(restartExpiry);
watch([notification, notificationType, () => store.state.ui.notification_expires], restartExpiry);
onUnmounted(clearExpiry);

const closeNotification = () => {
  store.commit("ui/notification_clear");
};
</script>

<style lang='scss' scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
@import "@/styles/layout.scss";

#notification-box {
  position: fixed;
  top: max(17px, env(safe-area-inset-top));
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  padding: 0 16px;
  pointer-events: none;
  z-index: 30000;
}

.ui-notification {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 360px;
  max-width: 100%;
  min-height: 58px;
  padding: 10px 10px 10px 18px;
  border: 1px solid $color-background-border;
  border-radius: 5px;
  background: $color-background-light;
  color: $color-text;
  box-shadow: 0 8px 24px rgba($color-text-dark, 0.35);
  pointer-events: auto;

  &.multiline {
    width: 600px;
    align-items: flex-start;

    .notification-icon { margin-top: 7px; }
    .message { padding-top: 5px; }
  }

  .notification-icon {
    flex: none;
    color: $color-green-chat;
  }

  &.loading .notification-icon { color: $color-secondary; }
  &.error .notification-icon { color: $color-private-chat; }

  .message {
    @include font-text-regular;
    flex: 1;
    min-width: 0;
    max-height: calc(100dvh - 90px - env(safe-area-inset-top));
    overflow-y: auto;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
    text-align: left;
    font-size: 14px;
    line-height: 1.5;
  }

  .notification-close {
    flex: none;
    color: $color-text-hex-70;

    &:hover {
      color: $color-text;
      background: $color-background-very-light;
    }

    @media ($mobile-site), (pointer: coarse) {
      width: 44px;
      height: 44px;
    }
  }
}

.notification-spinner { animation: notification-spin 1s linear infinite; }

@keyframes notification-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .notification-spinner { animation: none; }
}
</style>
