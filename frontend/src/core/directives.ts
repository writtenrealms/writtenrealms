import store from "@/store";
import Lookup from "@/components/game/lookup/Lookup.vue";
import {
  executePrimaryItemAction,
  isItemInActionContext,
} from "@/core/itemActions";
import type { ItemActionContext } from "@/core/itemActions";
import _ from "lodash";

interface InteractiveBindingValue {
  target?: any;
  side?: string;
  isLastMessage?: boolean;
  primaryAction?: boolean;
  actionContext?: ItemActionContext;
}

const bindingValues = new WeakMap<Element, InteractiveBindingValue>();
const cleanupFunctions = new WeakMap<Element, () => void>();
let hoverOwner: Element | null = null;
let lookupOwner: Element | null = null;

const currentBinding = (el: Element): InteractiveBindingValue => (
  bindingValues.get(el) || {}
);

const isInteractionEnabled = (el: Element) => {
  const binding = currentBinding(el);
  if (binding.isLastMessage === false || !binding.target) return false;
  if (binding.primaryAction && binding.actionContext) {
    return isItemInActionContext(
      binding.target,
      store.state.game,
      binding.actionContext,
    );
  }
  return true;
};

const clearOwnedInteractionState = (
  el: Element,
  forceLookupClear = false,
  clearLookup = true,
) => {
  const entity = currentBinding(el).target;
  if (!entity) return;

  if (hoverOwner === el) {
    if (store.state.game.hover_entity?.key === entity.key) {
      store.commit("game/hover_entity_set", null);
    }
    hoverOwner = null;
  }
  if (clearLookup && lookupOwner === el) {
    const ownsCurrentLookup = (
      store.state.game.lookup?.key === entity.key
    );
    if (
      ownsCurrentLookup &&
      (forceLookupClear || !store.state.game.popup_hover)
    ) {
      store.commit("game/lookup_clear");
      lookupOwner = null;
    } else if (!ownsCurrentLookup || forceLookupClear) {
      lookupOwner = null;
    }
  }
};

export const interactive = {
  beforeMount: (el, binding) => {
    bindingValues.set(el, binding.value || {});

    const onDebouncedMouseenter = _.debounce((event) => {
      if (!isInteractionEnabled(el)) return;
      if (!event.target.classList.contains('interactive')) return;
      if (store.state.game.is_mobile) return;
      const current = currentBinding(el);
      const entity = current.target;
      const side = current.side || "left";
      const rect = el.getBoundingClientRect();
      if (
        lookupOwner !== el ||
        !store.state.game.lookup ||
        entity.key !== store.state.game.lookup.key
      ) {
        store.commit("game/lookup_set", {
          key: entity.key,
          entity: entity,
          side: side,
          position: {
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
            left: rect.left,
          },
        });
      }
      lookupOwner = el;
    }, 150);

    const onDebouncedMouseleave = _.debounce((event) => {
      if (!event.target.classList.contains('interactive')) return;
      if (store.state.game.is_mobile) return;
      clearOwnedInteractionState(el);
    }, 150);

    const onClick = () => {
      if (!isInteractionEnabled(el)) return;
      const current = currentBinding(el);
      const entity = current.target;
      if (store.state.game.is_mobile) {
        store.commit("game/lookup_set", {
          key: entity.key,
          entity: entity,
        });
        store.commit("ui/modal/open_view", {
          component: Lookup,
          options: {
            closeOnOutsideClick: true,
          },
        });
        return;
      }
      if (current.primaryAction) {
        executePrimaryItemAction(
          store,
          entity,
          current.actionContext,
        );
      }
    };

    const onMouseenter = (event) => {
      if (!isInteractionEnabled(el)) return;
      if (!event.target.classList.contains('interactive')) return;
      onDebouncedMouseleave.cancel();
      store.commit("game/hover_entity_set", currentBinding(el).target);
      hoverOwner = el;
    };

    const onMouseleave = (event) => {
      if (!event.target.classList.contains('interactive')) return;
      onDebouncedMouseenter.cancel();
      clearOwnedInteractionState(el, false, false);
    };

    el.addEventListener("click", onClick);
    el.addEventListener("mouseenter", onMouseenter);
    el.addEventListener("mouseenter", onDebouncedMouseenter);
    el.addEventListener("mouseleave", onMouseleave);
    el.addEventListener("mouseleave", onDebouncedMouseleave);

    cleanupFunctions.set(el, () => {
      clearOwnedInteractionState(el, true);
      onDebouncedMouseenter.cancel();
      onDebouncedMouseleave.cancel();
      el.removeEventListener("click", onClick);
      el.removeEventListener("mouseenter", onMouseenter);
      el.removeEventListener("mouseenter", onDebouncedMouseenter);
      el.removeEventListener("mouseleave", onMouseleave);
      el.removeEventListener("mouseleave", onDebouncedMouseleave);
      bindingValues.delete(el);
      cleanupFunctions.delete(el);
    });
  },
  updated: (el, binding) => {
    const previousEntity = currentBinding(el).target;
    const nextEntity = binding.value?.target;
    if (
      previousEntity?.key &&
      previousEntity.key !== nextEntity?.key
    ) {
      clearOwnedInteractionState(el, true);
    }
    bindingValues.set(el, binding.value || {});
  },
  unmounted: (el) => {
    cleanupFunctions.get(el)?.();
  },
};
