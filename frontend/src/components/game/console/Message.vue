<template>
  <div v-if="message" :class="{ echo: message.echo }">
    <div
      v-for="(line, index) in lines"
      :key="index"
      :class="{ echo: message.echo, 'echo-line': message.echo }"
    >
      <span class="message-text">{{ line }}</span>
      <span
        v-if="index === lines.length - 1 && receiptPresentation"
        class="command-receipt-status"
        :class="{
          problem: receiptPresentation.problem,
          pending: receiptPresentation.state === 'pending',
          success: receiptPresentation.state === 'success',
        }"
      >
        <span
          class="command-receipt-sr"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {{ receiptPresentation.ariaLabel }}
        </span>
        <VTooltip
          v-model:shown="receiptDetailShown"
          :triggers="['hover', 'focus']"
          :hide-triggers="['hover', 'focus']"
          :auto-hide="true"
          :no-auto-focus="true"
          popper-class="command-receipt-tooltip"
        >
          <button
            type="button"
            class="command-receipt"
            :aria-label="receiptPresentation.ariaLabel"
            @click.stop="showReceiptDetail"
            @keydown.esc.stop.prevent="hideReceiptDetail"
          >
            <span aria-hidden="true">
              {{ receiptPresentation.text }}
            </span>
          </button>
          <template #popper>
            {{ receiptPresentation.ariaLabel }}
          </template>
        </VTooltip>
      </span>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed, ref, watch } from 'vue';
import { Tooltip as VTooltip } from 'floating-vue';
import { commandReceiptPresentation } from '@/core/commandReceipt';

const props = defineProps<{
  message: any;
}>();

const lines = computed(() => props.message.text.split("\n"));
const receiptPresentation = computed(() => (
  commandReceiptPresentation(props.message.command_receipt)
));
const receiptDetailShown = ref(false);

const showReceiptDetail = () => {
  receiptDetailShown.value = true;
};

const hideReceiptDetail = (event: KeyboardEvent) => {
  receiptDetailShown.value = false;
  (event.currentTarget as HTMLElement | null)?.blur();
};

watch(
  () => props.message.command_receipt?.updated_at,
  () => {
    receiptDetailShown.value = false;
  },
);
</script>

<style lang='scss' scoped>
@import "@/styles/colors.scss";
.echo {
  color: $color-text-hex-50;
}

.echo-line {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.65rem;
}

.message-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.command-receipt-status {
  display: inline-flex;
  align-items: baseline;
  color: $color-text-hex-50;
  white-space: nowrap;

  &.problem {
    color: $color-red;
  }

  &.success {
    color: $color-green;
  }
}

.command-receipt {
  appearance: none;
  display: inline-grid;
  place-items: center;
  min-width: 1.5rem;
  min-height: 1.5rem;
  padding: 0;
  margin: -0.3rem -0.2rem;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 0.9em;
  line-height: 1;
  white-space: nowrap;
  cursor: help;

  &:focus-visible {
    border-radius: 2px;
    outline: 1px solid currentColor;
    outline-offset: 2px;
  }
}

.command-receipt-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
