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
        <button
          v-if="receiptPresentation.failureDetail"
          type="button"
          class="command-receipt-error"
          :aria-label="receiptPresentation.ariaLabel"
          :aria-controls="failureDetailVisible ? failureDetailId : undefined"
          :aria-expanded="failureDetailVisible"
          @pointerenter="showFailureDetailOnHover"
          @pointerleave="failureDetailHovered = false"
          @focus="showFailureDetailOnFocus"
          @blur="closeFailureDetail"
          @click.stop="toggleFailureDetail"
          @keydown.esc.stop.prevent="blurReceiptError"
        >
          <span aria-hidden="true">
            {{ receiptPresentation.text }}
          </span>
        </button>
        <span
          v-else-if="receiptPresentation.state === 'success'"
          class="command-receipt-check"
          aria-hidden="true"
        >
          <svg viewBox="0 0 12 10" focusable="false">
            <polyline points="1,5 4.5,8.5 11,1" />
          </svg>
        </span>
        <span
          v-else
          class="command-receipt-pending"
          aria-hidden="true"
        >
          {{ receiptPresentation.text }}
        </span>
        <Teleport v-if="failureDetailVisible" to="body">
          <span
            :id="failureDetailId"
            class="command-receipt-detail"
            role="tooltip"
            :style="failureDetailStyle"
          >
            {{ receiptPresentation.failureDetail }}
          </span>
        </Teleport>
      </span>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed, nextTick, ref, watch } from 'vue';
import type { CSSProperties } from 'vue';
import { commandReceiptPresentation } from '@/core/commandReceipt';

const props = defineProps<{
  message: any;
}>();

const lines = computed(() => props.message.text.split("\n"));
const receiptPresentation = computed(() => (
  commandReceiptPresentation(props.message.command_receipt)
));
const failureDetailHovered = ref(false);
const failureDetailFocused = ref(false);
const failureDetailPinned = ref(false);
const failureDetailStyle = ref<CSSProperties>({
  left: "0px",
  top: "0px",
  visibility: "hidden",
});
const failureDetailId = computed(() => (
  `command-receipt-detail-${String(
    props.message.request_id || props.message.message_id || "message"
  ).replace(/[^a-zA-Z0-9_-]/g, "-")}`
));
const failureDetailVisible = computed(() => Boolean(
  receiptPresentation.value?.failureDetail &&
  (
    failureDetailHovered.value ||
    failureDetailFocused.value ||
    failureDetailPinned.value
  )
));

const positionFailureDetail = async (target?: EventTarget | null) => {
  const button = target instanceof HTMLElement
    ? target
    : null;
  if (!button) return;
  failureDetailStyle.value = {
    left: "0px",
    top: "0px",
    visibility: "hidden",
  };
  await nextTick();
  const detail = document.getElementById(failureDetailId.value);
  if (!failureDetailVisible.value || !detail) return;

  const viewportMargin = 16;
  const detailGap = 6;
  const buttonBounds = button.getBoundingClientRect();
  const detailBounds = detail.getBoundingClientRect();
  const maximumLeft = Math.max(
    viewportMargin,
    window.innerWidth - detailBounds.width - viewportMargin,
  );
  const left = Math.min(
    Math.max(buttonBounds.right - detailBounds.width, viewportMargin),
    maximumLeft,
  );
  const maximumTop = Math.max(
    viewportMargin,
    window.innerHeight - detailBounds.height - viewportMargin,
  );
  const aboveTop = buttonBounds.top - detailBounds.height - detailGap;
  const top = aboveTop >= viewportMargin
    ? aboveTop
    : Math.min(buttonBounds.bottom + detailGap, maximumTop);

  failureDetailStyle.value = {
    left: `${Math.round(left)}px`,
    top: `${Math.round(Math.max(viewportMargin, top))}px`,
    visibility: "visible",
  };
};

const showFailureDetailOnHover = (event: PointerEvent) => {
  if (event.pointerType === "touch") return;
  failureDetailHovered.value = true;
  void positionFailureDetail(event.currentTarget);
};

const showFailureDetailOnFocus = (event: FocusEvent) => {
  failureDetailFocused.value = true;
  void positionFailureDetail(event.currentTarget);
};

const closeFailureDetail = () => {
  failureDetailFocused.value = false;
  failureDetailPinned.value = false;
};

const toggleFailureDetail = (event: MouseEvent) => {
  if (failureDetailPinned.value) {
    failureDetailPinned.value = false;
    (event.currentTarget as HTMLElement | null)?.blur();
  } else {
    failureDetailPinned.value = true;
    void positionFailureDetail(event.currentTarget);
  }
};

const blurReceiptError = (event: KeyboardEvent) => {
  failureDetailHovered.value = false;
  closeFailureDetail();
  (event.currentTarget as HTMLElement | null)?.blur();
};

watch(
  () => props.message.command_receipt?.updated_at,
  () => {
    failureDetailHovered.value = false;
    closeFailureDetail();
    failureDetailStyle.value = {
      left: "0px",
      top: "0px",
      visibility: "hidden",
    };
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
  gap: 0.45rem;
}

.message-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.command-receipt-status {
  display: inline-flex;
  align-items: baseline;
  position: relative;
  color: $color-text-hex-50;
  white-space: nowrap;

  &.problem {
    color: $color-red;
  }
}

.command-receipt-error {
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
    border-radius: 50%;
    outline: 1px dotted currentColor;
    outline-offset: 1px;
  }
}

.command-receipt-detail {
  position: fixed;
  z-index: 35000;
  display: block;
  width: max-content;
  max-width: min(20rem, calc(100vw - 2rem));
  max-height: calc(100vh - 2rem);
  padding: 0.4rem 0.55rem;
  border: 1px solid $color-background-border;
  border-radius: 2px;
  background: $color-background-light;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.45);
  color: $color-text;
  font-size: 0.875rem;
  line-height: 1.25;
  overflow-y: auto;
  overflow-wrap: anywhere;
  pointer-events: none;
  white-space: normal;
}

.command-receipt-check {
  display: inline-flex;
  width: 0.72rem;
  height: 0.62rem;
  color: $color-text-hex-30;
  transform: translateY(0.04rem);

  svg {
    display: block;
    width: 100%;
    height: 100%;
    overflow: visible;
  }

  polyline {
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: square;
    stroke-linejoin: miter;
  }
}

.command-receipt-pending {
  color: inherit;
  font-size: 0.9em;
  line-height: 1;
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
