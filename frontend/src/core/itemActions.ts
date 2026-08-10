import _ from "lodash";

import { formatMoneyUppercaseCurrency } from "@/core/economy.ts";
import {
  getRoomMerchantProvider,
  merchantProviderTarget,
} from "@/core/merchantProviders";

export interface ItemAction {
  action: string;
  label: string;
}

export interface ItemActionChoices {
  primaryAction?: ItemAction;
  actions: ItemAction[];
}

export type ItemActionContext = "room" | "inventory" | "equipment";

const actionContextSnapshots = new WeakMap<
  object,
  Map<string, ItemActionContext>
>();

export const EQUIPMENT_SLOTS = [
  "weapon",
  "offhand",
  "head",
  "body",
  "arms",
  "hands",
  "waist",
  "legs",
  "feet",
  "accessory",
];

export const KNOWN_ITEM_ACTIONS = [
  "eat",
  "buy",
  "buyback",
  "sell",
  "wield",
  "wear",
  "use",
  "remove",
  "get",
  "drop",
  "salvage",
  "get_from",
  "get_all_from",
];

const ITEM_ACTION_PRIORITY: ItemAction[] = [
  { action: "eat", label: "EAT" },
  { action: "buy", label: "BUY" },
  { action: "buyback", label: "BUY BACK" },
  { action: "sell", label: "SELL" },
  { action: "wield", label: "WIELD" },
  { action: "wear", label: "WEAR" },
  { action: "use", label: "USE" },
  { action: "remove", label: "REMOVE" },
  { action: "get", label: "GET" },
  { action: "salvage", label: "SALVAGE" },
  { action: "drop", label: "DROP" },
  { action: "get_from", label: "GET FROM" },
  { action: "get_all_from", label: "GET ALL FROM" },
];

const KNOWN_ITEM_ACTION_SET = new Set(
  ITEM_ACTION_PRIORITY.map((action) => action.action),
);

export const getItemKeyword = (entity: any) => {
  if (entity && entity.keyword) return entity.keyword;
  if (!entity || !entity.keywords) return "";
  return entity.keywords.split(" ")[0];
};

const actionsAsMap = (
  actions: any,
  knownActions: string[],
): Record<string, any> => {
  const mapped: Record<string, any> = {};
  for (const action of knownActions) {
    mapped[action] = false;
  }

  if (Array.isArray(actions)) {
    for (const action of actions) {
      mapped[action] = true;
    }
    return mapped;
  }

  if (actions && typeof actions === "object") {
    for (const [action, value] of Object.entries(actions)) {
      mapped[action] = value;
    }
  }

  return mapped;
};

export const findItemByKey = (items: any[], key: string): any => {
  if (!items) return null;

  for (const item of items) {
    if (!item) continue;
    if (item.key === key) return item;
    const nested = findItemByKey(item.inventory || [], key);
    if (nested) return nested;
  }

  return null;
};

export const findEquipmentItemByKey = (
  equipment: any,
  key: string,
): any => {
  if (!equipment) return null;

  for (const slot of EQUIPMENT_SLOTS) {
    const item = equipment[slot];
    if (!item) continue;
    if (item.key === key) return item;
    const nested = findItemByKey(item.inventory || [], key);
    if (nested) return nested;
  }

  return null;
};

const isTopLevelItem = (items: any[], key: string) => {
  if (!items) return false;
  return items.some((item) => item && item.key === key);
};

export const isItemInActionContext = (
  item: any,
  gameState: any,
  context?: ItemActionContext,
) => {
  if (!context) return true;
  if (!item || !item.key) return false;

  if (context === "room") {
    return isTopLevelItem(gameState.room?.inventory || [], item.key);
  }
  if (context === "inventory") {
    return isTopLevelItem(gameState.player?.inventory || [], item.key);
  }
  if (context === "equipment") {
    return Boolean(
      findEquipmentItemByKey(gameState.player?.equipment, item.key),
    );
  }
  return false;
};

export const getItemActionContextSnapshot = (
  owner: object,
  gameState: any,
) => {
  const existing = actionContextSnapshots.get(owner);
  if (existing) return existing;

  const snapshot = new Map<string, ItemActionContext>();
  for (const item of gameState.player?.inventory || []) {
    if (item?.key) snapshot.set(item.key, "inventory");
  }
  for (const item of Object.values(
    gameState.player?.equipment || {},
  )) {
    if ((item as any)?.key) {
      snapshot.set((item as any).key, "equipment");
    }
  }
  actionContextSnapshots.set(owner, snapshot);
  return snapshot;
};

const getContainerKey = (item: any) => {
  const inContainer = item && item.in_container;
  if (!inContainer) return "";
  if (typeof inContainer === "string") return inContainer;
  return inContainer.key || "";
};

export const normalizeItemActions = (item: any, gameState: any) => {
  const actions = actionsAsMap(item.actions, KNOWN_ITEM_ACTIONS);
  const room = gameState.room || {};
  const player = gameState.player || {};
  const roomChars = room.chars || [];
  const roomMerchantProvider = getRoomMerchantProvider(room);

  const inRoom = isTopLevelItem(room.inventory || [], item.key);
  const inInventory = isTopLevelItem(player.inventory || [], item.key);
  const inEquipment =
    !inInventory &&
    Boolean(findEquipmentItemByKey(player.equipment, item.key));

  const isContainer =
    item.is_container === true ||
    item.type === "container" ||
    item.type === "corpse";

  const hasMerchant = Boolean(roomMerchantProvider) || roomChars.some(
    (char) => char && char.is_merchant,
  );
  if (inRoom) {
    if (item.is_pickable !== false) actions.get = true;
    if (isContainer) actions.get_from = true;
  } else if (inInventory) {
    actions.drop = true;
    const hasContents =
      Array.isArray(item.inventory) && item.inventory.length > 0;
    if (
      item.is_salvageable === true &&
      item.type !== "quest" &&
      !hasContents
    ) {
      actions.salvage = true;
    }

    if (item.type === "equippable") {
      if (item.equipment_type && item.equipment_type.startsWith("weapon")) {
        actions.wield = true;
      } else if (item.equipment_type) {
        actions.wear = true;
      }
    }

    if (isContainer) actions.get_from = true;
    if (item.type === "food") actions.eat = true;
    if (item.on_use_cmd) actions.use = true;

    if (item.value && hasMerchant) {
      actions.sell = true;
      const merchantTarget = merchantProviderTarget(roomMerchantProvider);
      if (merchantTarget && !item.sell_command) {
        item.sell_command = `sell ${item.key} to ${merchantTarget}`;
      }
    }
  } else if (inEquipment) {
    actions.remove = true;
    if (item.on_use_cmd) actions.use = true;
  }

  const containerKey = getContainerKey(item);
  if (containerKey.startsWith("mob.")) {
    const merchant = roomChars.find(
      (char) => char && char.key === containerKey && char.is_merchant,
    );
    if (merchant) {
      actions.buy = true;
    }
  } else if (
    !inRoom &&
    !inInventory &&
    !inEquipment &&
    hasMerchant &&
    item.value
  ) {
    actions.buy = true;
  }

  if (item.buyback_command) {
    actions.buy = false;
  }

  return actions;
};

export const normalizeLookupItem = (sourceItem: any, gameState: any) => {
  const item = _.cloneDeep(sourceItem || {});

  if (!item.keywords) item.keywords = "";
  if (!item.keyword) item.keyword = getItemKeyword(item);
  if (!item.description) {
    item.description = item.name
      ? `It is ${item.name}.`
      : "It is an item.";
  }
  if (item.is_container === undefined) {
    item.is_container =
      item.type === "container" || item.type === "corpse";
  }
  if (!Array.isArray(item.inventory)) item.inventory = [];
  item.actions = normalizeItemActions(item, gameState);

  return item;
};

const hasAction = (item: any, actionCode: string) => {
  const itemActions = item && item.actions;
  if (!itemActions) return false;
  if (Array.isArray(itemActions)) {
    return itemActions.indexOf(actionCode) !== -1;
  }
  return Boolean(itemActions[actionCode]);
};

export const buildItemActionChoices = (
  item: any,
  gameState: any,
): ItemActionChoices => {
  let actions: ItemAction[] = [];

  for (const action of ITEM_ACTION_PRIORITY) {
    if (!hasAction(item, action.action)) continue;

    const actionData = { ...action };
    if (
      action.action === "buy"
      || action.action === "buyback"
      || action.action === "sell"
    ) {
      const actionPrice = action.action === "buyback"
        ? item.buyback_price || item.price || item.value
        : action.action === "sell"
          ? item.sell_price
          : item.buy_price || item.price || item.value;
      if (
        actionPrice
        && typeof actionPrice === "object"
        && "amount" in actionPrice
      ) {
        const verb = action.action === "buyback"
          ? "BUY BACK FOR"
          : action.action === "sell" ? "SELL FOR" : "BUY FOR";
        actionData.label = `${verb} ${formatMoneyUppercaseCurrency(
          actionPrice,
          gameState.world?.economy,
        )}`;
      }
    }

    if (item.is_container && action.action === "get_from") {
      const isUnpickableRoomContainer = (
        item.is_pickable === false &&
        isItemInActionContext(item, gameState, "room")
      );
      if (
        !item.inventory.length &&
        !isUnpickableRoomContainer &&
        !item.corpse_id
      ) {
        continue;
      }
      if (item.corpse_id) {
        actionData.label = "LOOT";
      }
    }

    actions.push(actionData);
  }

  const actionMap = (
    item &&
    item.actions &&
    !Array.isArray(item.actions)
  ) ? item.actions : {};
  for (const [actionCode, value] of Object.entries(actionMap)) {
    if (KNOWN_ITEM_ACTION_SET.has(actionCode)) continue;
    if (!value) continue;
    actions.push({
      action: actionCode,
      label: actionCode.toUpperCase(),
    });
  }

  if (hasAction(item, "salvage")) {
    const salvageActionPriority = new Map([
      ["salvage", 0],
      ["drop", 1],
    ]);
    actions.sort(
      (left, right) =>
        (salvageActionPriority.get(left.action) ?? 2) -
        (salvageActionPriority.get(right.action) ?? 2),
    );
  }

  const [primaryAction, ...secondaryActions] = actions;
  return {
    primaryAction,
    actions: secondaryActions,
  };
};

const closeItemLookup = (store: any) => {
  store.commit("game/lookup_clear");
  store.commit("ui/modal/close");
};

export const executeItemAction = (
  store: any,
  item: any,
  action: string,
) => {
  const rawAction = String(action || "").trim();
  if (!rawAction) return false;

  const normalizedAction = rawAction.toLowerCase();
  let authoredCommand = "";
  if (normalizedAction === "buy") {
    authoredCommand = item.buy_command || `buy ${item.key}`;
  } else if (normalizedAction === "sell") {
    authoredCommand = item.sell_command || `sell ${item.key}`;
  } else if (normalizedAction === "buyback") {
    authoredCommand = item.buyback_command || "";
  }
  if (authoredCommand) {
    store.dispatch("game/cmd", authoredCommand);
    closeItemLookup(store);
    return true;
  }

  if (!KNOWN_ITEM_ACTIONS.includes(normalizedAction)) {
    store.dispatch("game/cmd", rawAction);
    closeItemLookup(store);
    return true;
  }

  const itemKeyword = getItemKeyword(item);
  let inRoom = false;
  let duplicateCount = 0;
  for (const roomItem of store.state.game.room?.inventory || []) {
    if (roomItem.key === item.key) {
      inRoom = true;
      break;
    }
    const foundIndex = (roomItem.keywords || "")
      .split(" ")
      .indexOf(itemKeyword);
    if (foundIndex !== -1) {
      duplicateCount += 1;
    }
  }

  let inInventory = false;
  if (!inRoom) {
    duplicateCount = 0;
    for (const inventoryItem of store.state.game.player?.inventory || []) {
      if (inventoryItem.key === item.key) {
        inInventory = true;
        break;
      }
      const foundIndex = (inventoryItem.keywords || "")
        .split(" ")
        .indexOf(itemKeyword);
      if (foundIndex !== -1) {
        duplicateCount += 1;
      }
    }
  }

  let target = itemKeyword || item.name;
  if ((inRoom || inInventory) && duplicateCount > 0) {
    target = `${duplicateCount + 1}.${target}`;
  }

  const payload: any = {
    type: `cmd.${normalizedAction}`,
    data: {
      item: { key: item.key },
    },
    text: `${normalizedAction} ${target}`,
  };

  if (
    normalizedAction === "get_from" ||
    normalizedAction === "get_all_from"
  ) {
    payload.data.from = { key: item.key };
    payload.data.item = { name: "all" };
    payload.type = "cmd.get";
    payload.text = `get all ${target}`;
  }

  store.dispatch("game/cmd_structured", payload);
  closeItemLookup(store);
  return true;
};

export const executePrimaryItemAction = (
  store: any,
  sourceItem: any,
  actionContext?: ItemActionContext,
) => {
  if (!sourceItem || store.state.game.is_mobile) return false;
  if (
    !isItemInActionContext(
      sourceItem,
      store.state.game,
      actionContext,
    )
  ) {
    return false;
  }

  const item = normalizeLookupItem(sourceItem, store.state.game);
  const { primaryAction } = buildItemActionChoices(
    item,
    store.state.game,
  );
  if (!primaryAction) return false;

  return executeItemAction(store, item, primaryAction.action);
};
