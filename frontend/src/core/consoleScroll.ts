type ScrollViewport = Pick<HTMLElement, 'scrollTop' | 'scrollHeight' | 'clientHeight'>;

const BOTTOM_TOLERANCE = 4;

// Follow intent survives layout changes; a newly exposed gap is not itself a
// request to read scrollback. The component observes both content and viewport.
export const createConsoleScrollFollower = (
  viewport: ScrollViewport,
  onDistance: (distance: number) => void,
) => {
  const measure = () => {
    const height = viewport.scrollHeight;
    const visible = viewport.clientHeight;
    const maximum = Math.max(0, height - visible);
    const top = Math.max(0, Math.min(maximum, viewport.scrollTop));
    return { height, visible, maximum, top, gap: maximum - top };
  };
  let previous = measure();
  let following = true;
  let publishedDistance = -1;

  const publish = (gap: number) => {
    const distance = following || gap <= BOTTOM_TOLERANCE ? 0 : gap;
    if (distance !== publishedDistance) {
      publishedDistance = distance;
      onDistance(distance);
    }
  };

  const scrollToBottom = () => {
    following = true;
    viewport.scrollTop = viewport.scrollHeight;
    previous = measure();
    publish(0);
  };

  const onScroll = () => {
    const current = measure();
    const layoutChanged = current.height !== previous.height
      || current.visible !== previous.visible;
    // Shrinking content/a larger viewport can clamp scrollTop upward. Only
    // upward movement beyond that clamp indicates scrolling during a reflow.
    const clampedPreviousTop = Math.min(previous.top, current.maximum);
    if (!layoutChanged || current.top < clampedPreviousTop - BOTTOM_TOLERANCE) {
      following = current.gap <= BOTTOM_TOLERANCE;
    }
    previous = current;
    publish(current.gap);
  };

  const onLayoutChange = () => {
    if (following) {
      scrollToBottom();
    } else {
      previous = measure();
      following = previous.gap <= BOTTOM_TOLERANCE;
      publish(previous.gap);
    }
  };

  return { scrollToBottom, onScroll, onLayoutChange };
};
