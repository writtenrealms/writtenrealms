import { onBeforeUnmount, shallowRef } from "vue";

export interface ItemPreview {
  item: any;
  anchor: HTMLElement;
  centered: boolean;
  focus: boolean;
}

export function itemPreviewPosition(
  anchor: Pick<DOMRect, "top" | "bottom" | "left" | "right">,
  panel: Pick<DOMRect, "width" | "height">,
  viewport: { width: number; height: number },
) {
  const gutter = 12;
  let left = anchor.right + gutter;
  let top = anchor.top;
  if (left + panel.width > viewport.width - gutter) {
    left = anchor.left - panel.width - gutter;
    if (left < gutter) {
      left = anchor.left;
      top = anchor.bottom + gutter + panel.height <= viewport.height - gutter
        ? anchor.bottom + gutter
        : anchor.top - panel.height - gutter;
    }
  }
  return {
    left: `${Math.max(gutter, Math.min(left, viewport.width - panel.width - gutter))}px`,
    top: `${Math.max(gutter, Math.min(top, viewport.height - panel.height - gutter))}px`,
  };
}

// A page owns one preview, regardless of how many items it displays.
export function useItemPreview() {
  const preview = shallowRef<ItemPreview | null>(null);
  let closeTimer: ReturnType<typeof setTimeout> | undefined;

  const keepOpen = () => clearTimeout(closeTimer);
  const close = () => {
    keepOpen();
    preview.value = null;
  };
  const leave = () => {
    keepOpen();
    if (!preview.value?.centered) closeTimer = setTimeout(close, 150);
  };
  const show = (item: any, event: Event, centered: boolean, focus = false) => {
    keepOpen();
    preview.value = { item, anchor: event.currentTarget as HTMLElement, centered, focus };
  };
  // Match the game's mobile breakpoint, including mouse emulation on mobile.
  const isMobile = () => typeof window !== "undefined"
    && window.matchMedia("(max-width: 768px), (hover: none)").matches;
  const hover = (item: any, event: PointerEvent) => {
    if (event.pointerType !== "mouse" || isMobile() || preview.value?.centered) return;
    show(item, event, false);
  };
  const focus = (item: any, event: FocusEvent) => {
    if (preview.value?.centered) return;
    if ((event.currentTarget as HTMLElement).matches(":focus-visible")) show(item, event, false);
  };
  const activate = (item: any, event: MouseEvent) => {
    const centered = (event as PointerEvent).pointerType === "touch" || isMobile();
    show(item, event, centered, true);
  };
  const bindings = (item: any) => ({
    "aria-haspopup": "dialog" as const,
    "aria-expanded": preview.value?.item.key === item.key,
    "aria-controls": preview.value?.item.key === item.key ? "item-preview" : undefined,
    onPointerenter: (event: PointerEvent) => hover(item, event),
    onPointerleave: leave,
    onFocus: (event: FocusEvent) => focus(item, event),
    onBlur: leave,
    onClick: (event: MouseEvent) => activate(item, event),
  });

  onBeforeUnmount(close);
  return { preview, bindings, keepOpen, leave, close };
}
