import axios from "axios";
import { FORGE_WS_URI } from "@/config";
import type {
  CurrencyBalancesChangedData,
  PlayerEconomy,
} from "@/core/economy";
import {
  COMMAND_RECEIPT_TIMEOUT_MS,
  commandResolution,
  commandResolutionEchoIndex,
  commandRequestId,
  commandRequestSegments,
  commandTerminalResult,
  commandTriggerRejectionResult,
  createCommandRequestId,
  initialCommandReceipt,
  transitionCommandReceipt,
} from "@/core/commandReceipt";
import {
  cancelAllEditRoomTabs,
  cancelEditRoomTab,
  openResolvedEditRoomTab,
  prepareEditRoomTab,
} from "@/core/editRoomCommand";
import { builderRoomIndexRoute } from "@/core/builderRoutes";
import { playerRoundEffectSnapshot } from "@/core/roundEffects";
import _ from "lodash";
import router from "@/router";


// Want to make sure we can read THIS
// Running one more test

const MESSAGE_LIMIT = 200;
const TRIGGER_ITEMS_CHANGED_MESSAGE = "notification.trigger.items_changed";
const TRIGGER_MOBS_CHANGED_MESSAGE = "notification.trigger.mobs_changed";
const ABILITY_PREPARATIONS_UPDATE_MESSAGE = "player.ability_preparations.update";
const ABILITY_PLAYER_STATE_MESSAGES = new Set([
  "cmd.ability.learn.success",
  "cmd.ability.unlearn.success",
  "cmd.ability.hotkey.success",
  "player.abilities.update",
]);
const COMMAND_REQUEST_QUEUED_MESSAGE = "cmd.request.queued";
const COMMAND_REQUEST_COMPLETED_MESSAGE = "cmd.request.completed";
const TRIGGER_ACCEPTED_MESSAGE = "cmd.trigger.accepted";
const TRIGGER_COMPLETED_MESSAGE = "cmd.trigger.completed";
const TRIGGER_CANCELLED_MESSAGE = "cmd.trigger.cancelled";
const TRIGGER_REJECTED_MESSAGE = "cmd.trigger.rejected";
const MERCHANT_INVENTORY_ADD_MESSAGES = new Set([
  "cmd.buy.success",
  "cmd.buyback.success",
]);
const MERCHANT_INVENTORY_REMOVE_MESSAGES = new Set([
  "cmd.sell.success",
]);

const commandReceiptTimers = new Map<string, ReturnType<typeof setTimeout>>();

const clearCommandReceiptTimer = (requestId: string, timerName: string) => {
  const key = `${requestId}:${timerName}`;
  const timer = commandReceiptTimers.get(key);
  if (timer) {
    clearTimeout(timer);
    commandReceiptTimers.delete(key);
  }
};

const clearCommandReceiptTimers = (requestId?: string) => {
  for (const [key, timer] of commandReceiptTimers) {
    if (!requestId || key.startsWith(`${requestId}:`)) {
      clearTimeout(timer);
      commandReceiptTimers.delete(key);
    }
  }
};

const scheduleCommandReceiptTransition = (
  commit,
  requestId: string,
  timerName: string,
  delay: number,
  transition,
) => {
  const key = `${requestId}:${timerName}`;
  const existingTimer = commandReceiptTimers.get(key);
  if (existingTimer) {
    clearTimeout(existingTimer);
  }
  const timer = setTimeout(() => {
    if (commandReceiptTimers.get(key) !== timer) return;
    commandReceiptTimers.delete(key);
    commit("command_receipt_update", {
      request_id: requestId,
      ...transition,
    });
  }, delay);
  commandReceiptTimers.set(key, timer);
};

const itemKey = (item: any): string => String(item?.key ?? "");

const removeItemsByKeyInPlace = (inventory: any[], removedItems: any[]) => {
  const removedKeys = new Set(
    (Array.isArray(removedItems) ? removedItems : [])
      .map(itemKey)
      .filter(Boolean),
  );
  if (!removedKeys.size) return;

  for (let index = inventory.length - 1; index >= 0; index -= 1) {
    if (removedKeys.has(itemKey(inventory[index]))) {
      inventory.splice(index, 1);
    }
  }
};

const applyItemChangesInPlace = (
  inventory: any[],
  removedItems: any[],
  addedItems: any[],
) => {
  removeItemsByKeyInPlace(inventory, removedItems);
  for (const item of Array.isArray(addedItems) ? addedItems : []) {
    const key = itemKey(item);
    if (!key) continue;
    const existingIndex = inventory.findIndex(
      existingItem => itemKey(existingItem) === key,
    );
    if (existingIndex === -1) {
      inventory.push(item);
    } else {
      inventory.splice(existingIndex, 1, item);
    }
  }
};

interface PlayerEconomyCarrier {
  economy?: PlayerEconomy | null;
  [key: string]: unknown;
}

const walletEventMatchesPlayer = (
  player: any,
  payload: CurrencyBalancesChangedData,
) => {
  if (!player || !payload) return false;
  const eventPlayer = payload.player;
  const eventPlayerId = eventPlayer && typeof eventPlayer === "object"
    ? eventPlayer.id
    : eventPlayer;
  const eventPlayerKey = eventPlayer && typeof eventPlayer === "object"
    ? eventPlayer.key
    : eventPlayer;
  return (
    String(eventPlayerId ?? "") === String(player.id ?? "") ||
    String(eventPlayerKey ?? "") === String(player.key ?? "")
  );
};

const playerWithCurrentEconomyWhenNewer = (
  currentPlayer: PlayerEconomyCarrier | null | undefined,
  incomingPlayer: PlayerEconomyCarrier,
): PlayerEconomyCarrier => {
  const currentEconomy = currentPlayer?.economy;
  const incomingEconomy = incomingPlayer?.economy;
  if (!currentEconomy || !incomingEconomy) return incomingPlayer;

  const currentRevision = currentEconomy.wallet_revision;
  const incomingRevision = incomingEconomy.wallet_revision;
  if (
    Number.isSafeInteger(currentRevision) &&
    Number.isSafeInteger(incomingRevision) &&
    incomingRevision < currentRevision
  ) {
    return {
      ...incomingPlayer,
      economy: currentEconomy,
    };
  }
  return incomingPlayer;
};

const cloneRoomChar = (char) => ({
  ...char,
  target: char && char.target ? { ...char.target } : char?.target,
});

const replaceRoomChars = (state, chars) => {
  state.room_chars = chars;
  if (state.room) {
    state.room.chars = chars;
  }
};

const applyRoomCharChanges = (chars: any[], changedChars: any[]) => {
  const updates = new Map(
    (Array.isArray(changedChars) ? changedChars : [])
      .filter(char => char && char.key)
      .map(char => [String(char.key), char]),
  );
  if (!updates.size) return chars;
  return (Array.isArray(chars) ? chars : []).map(char => {
    const update = updates.get(String(char?.key ?? ""));
    return update ? cloneRoomChar({ ...char, ...update }) : char;
  });
};

const set_initial_state = () => {
  return {
    player_id: null,
    world_id: null,
    // uri: WS_URI,
    forge_ws_uri: FORGE_WS_URI,
    room_key: null,
    is_mobile: false,
    width: 0,

    is_connected: false,
    websocket: null,
    messages: [],
    received_event_ids: {},
    received_event_id_order: [],

    // We want to keep track of the last received message for the various
    // message types, so that for example we only make the most recent look rom
    // message interactable (same thing for inventory, equipment etc).
    last_viewed_room_message: null,

    last_message: {},

    // Data about the item or mob being displayed in Hover or Modal
    lookup: null,

    // objects
    world: null,

    /*
       Player data, a lot of which we break out into its own state because if
       we keep everything in player, any update to it triggers the reaction
       updates.
    */
    player: null,
    wallet_sync_requested: false,
    player_effects: [],
    player_level: 0,
    player_archetype: "",

    player_config: {},

    // assassins
    player_stance: "",

    // Slugs of abilities currently prepared for an encounter round.
    prepared_abilities: [],

    // The player target is set by a kill command going through, or by
    // a notification.attack command being received, at which point
    // we look at the player.targer variable. The distinction is important
    // because sometimes player.target doesn't get set right away, or gets
    // cleared momentarily, and we want the UI to be more robust than that.
    player_target: null,

    focus_data: {},

    // Master record for all tracked effects, keyed by character key
    effects: {},

    // Index of casts / channels that have been started and therefore should
    // not be re-animated. Indexed by expires timestamp, since it should be
    // unique per cast.
    started_casts: {},

    // Value to keep track of what entity the user is currently hovering over,
    // if any. This is to assist in the closing or non-closing of hover popups
    // on desktop.
    hover_entity: null,

    // Value to keep track of whether a user is hovering over the desktop
    // popup so that mousing out of the interactive directive doesn't close
    // if that's where the user went.
    popup_hover: false,

    map: null,
    map_hash: {},
    room: null,

    hint: null,
    full_screen_message: null,
    transfer_to: {},
    // Key of the last character that's died
    last_death: null,

    // Sidebar data

    who_list: [],
    com_list: [],
    factions: [],
    room_chars: [],
    motd: '',
  };
};

const receiveMessage = async ({
  event,
  rootState,
  state,
  dispatch,
  commit,
}) => {
  /* Main process for receiving messages */
  const message_data = JSON.parse(event.data);
  const eventId = message_data?.data?._event_id;
  if (eventId && state.received_event_ids[eventId]) {
    return;
  }
  if (eventId) {
    commit("event_id_seen", eventId);
  }
  const requestId = commandRequestId(message_data);
  const resolution = commandResolution(message_data);
  const requestSegments = commandRequestSegments(message_data);

  if (resolution) {
    const echoIndex = commandResolutionEchoIndex(state.messages, resolution);
    if (echoIndex !== -1) {
      const echoRequestId = state.messages[echoIndex]?.request_id;
      if (echoRequestId) {
        clearCommandReceiptTimer(echoRequestId, "unconfirmed");
      }
      commit("command_echo_resolve", {
        index: echoIndex,
        resolution: message_data,
      });
      commit("last_message_set", message_data);
      return;
    }
  }

  if (requestSegments) {
    clearCommandReceiptTimer(requestSegments.requestId, "unconfirmed");
    for (const requestSegment of requestSegments.requestSegments) {
      commit("command_receipt_update", {
        request_id: requestSegments.requestId,
        request_segment: requestSegment,
        segment_status: "accepted",
      });
    }
    return;
  }

  // Receipt events update the locally echoed command in place. Gateway and
  // accepted lifecycle messages are transport control frames, not prose.
  if (message_data.type === COMMAND_REQUEST_QUEUED_MESSAGE) {
    if (requestId) {
      clearCommandReceiptTimer(requestId, "unconfirmed");
      commit("command_receipt_update", {
        request_id: requestId,
        phase: "received",
      });
    }
    return;
  }

  if (message_data.type === TRIGGER_ACCEPTED_MESSAGE) {
    if (requestId) {
      clearCommandReceiptTimer(requestId, "unconfirmed");
      commit("command_receipt_update", {
        request_id: requestId,
        request_segment: message_data.data?.request_segment,
        segment_status: "accepted",
      });
    }
    return;
  }

  if (message_data.type === TRIGGER_COMPLETED_MESSAGE) {
    if (requestId) {
      clearCommandReceiptTimers(requestId);
      commit("command_receipt_update", {
        request_id: requestId,
        request_segment: message_data.data?.request_segment,
        segment_status: "completed",
      });
    }
    return;
  }

  if (message_data.type === TRIGGER_CANCELLED_MESSAGE) {
    const cancellationResult = commandTerminalResult(message_data);
    if (cancellationResult) {
      clearCommandReceiptTimers(cancellationResult.requestId);
      commit("command_receipt_update", {
        request_id: cancellationResult.requestId,
        request_segment: cancellationResult.requestSegment,
        segment_status: cancellationResult.segmentStatus,
        message: cancellationResult.message,
      });
    }
    return;
  }

  if (message_data.type === TRIGGER_REJECTED_MESSAGE) {
    const rejectionResult = commandTriggerRejectionResult(message_data);
    if (rejectionResult) {
      clearCommandReceiptTimers(rejectionResult.requestId);
      commit("command_receipt_update", {
        request_id: rejectionResult.requestId,
        request_segment: rejectionResult.requestSegment,
        segment_status: rejectionResult.segmentStatus,
        message: rejectionResult.message,
      });
    }
    if (!message_data.text) {
      return;
    }
  } else if (
    requestId &&
    message_data.data?.code === "command_delivery_unconfirmed"
  ) {
    cancelEditRoomTab(requestId);
    clearCommandReceiptTimers(requestId);
    commit("command_receipt_update", {
      request_id: requestId,
      phase: "unconfirmed",
      message: message_data.data?.error || message_data.text,
    });
  } else {
    const terminalResult = commandTerminalResult(message_data);
    if (terminalResult) {
      clearCommandReceiptTimers(terminalResult.requestId);
      commit("command_receipt_update", {
        request_id: terminalResult.requestId,
        request_segment: terminalResult.requestSegment,
        segment_status: terminalResult.segmentStatus,
        message: terminalResult.message,
      });
      if (message_data.type === COMMAND_REQUEST_COMPLETED_MESSAGE) {
        return;
      }
    }
  }

  // Keep track of communication messages for the coms log
  const com_messages = [
    "cmd.chat.success",
    "notification.cmd.chat.success",
    "cmd.tell.success",
    "notification.tell",
    "cmd.reply.success",
    "notification.reply",
    "cmd.cchat.success",
    "notification.cmd.cchat.success",
    "cmd.gossip.success",
    "notification.cmd.gossip.success",
  ];
  if (com_messages.indexOf(message_data.type) != -1) {
    commit("com_list_add", message_data);
  }

  const skip_messages = [
    "notification.shorttic",
    "notification.longtic",
    "notification.who",
    "player.abilities.update",
    ABILITY_PREPARATIONS_UPDATE_MESSAGE,
    "player.combat_effects.update",
    "notification.regen",
    "currency.balances_changed",
    TRIGGER_ITEMS_CHANGED_MESSAGE,
    TRIGGER_MOBS_CHANGED_MESSAGE,
  ];

  // Add visible messages to the console history. message_add establishes the
  // immutable snapshot boundary before live state processing begins.
  if (skip_messages.indexOf(message_data.type) == -1) {
    commit("message_add", message_data);
    const historyMessage = state.messages[state.messages.length - 1];
    console.log(`RECV ${historyMessage.type}`);
    console.log(historyMessage);
  }


  /* Special messages processing */

  // Keep track for each type of message of the last one seen. This is useful
  // to have advanced actions be available only on the latest of a series of
  // messages. For example, actions on items in inventory & eq views
  commit("last_message_set", message_data);

  if (message_data.type === "cmd./edit.success") {
    try {
      const route = builderRoomIndexRoute(
        message_data.data?.world_id,
        message_data.data?.room,
      );
      const href = router.resolve(route).href;
      const opened = openResolvedEditRoomTab(requestId, href);
      if (!opened) {
        commit(
          "ui/notification_set_error",
          "Your browser blocked the room editor tab. Allow pop-ups and run /edit again.",
          { root: true },
        );
      }
    } catch (error) {
      cancelEditRoomTab(requestId);
      console.error("Unable to open the resolved room editor route.", error);
      commit(
        "ui/notification_set_error",
        "The room editor route could not be opened.",
        { root: true },
      );
    }
  } else if (message_data.type === "cmd./edit.error") {
    cancelEditRoomTab(requestId);
  }

  const isConnectSuccess = message_data.type === "system.connect.success";
  const isStateSync = message_data.type === "cmd.state.sync.success";
  const isStateSnapshot =
    isStateSync ||
    (isConnectSuccess && message_data.data && message_data.data.room);
  const isCompletedFleeSuccess =
    message_data.type === "cmd.flee.success" &&
    message_data.data &&
    message_data.data.room;
  const isCombatDisengage =
    message_data.type === "cmd.disengage.success" ||
    message_data.type === "notification.combat.disengage";

  // Connection acknowledged (no state payload yet)
  if (isConnectSuccess) {
    commit("connected_set");
    commit("full_screen_message_set", "Loading world...");
    commit("ui/notification_set", "Connected.", { root: true });
    if (!isStateSnapshot) {
      return;
    }
  }

  // Initial/full state payload
  if (isStateSnapshot) {
    commit("connected_set");
    const map = {};
    const map_list = message_data.data.map || [];
    for (const room of map_list) {
      map[room.key] = room;
    }
    commit("set_map", map);
    commit("room_set", message_data.data.room);
    if (message_data.data.room && message_data.data.room.key) {
      commit("set_room_key", message_data.data.room.key);
    }
    commit("last_viewed_room_message_set", message_data);
    const world_data = {
      ...state.world,
      ...message_data.data.world,
    };
    commit("world_set", world_data);
    commit("player_set", message_data.data.actor);
    commit("prepared_abilities_set", message_data.data.prepared_abilities);
    commit("wallet_sync_requested_clear");
    commit("who_list_set", message_data.data.who_list);
    commit("full_screen_message_clear");
    router.push({ name: "game" });

  } else if (message_data.type == "system.disconnect.error") {
    commit("full_screen_message_clear");
  }

  if (message_data.data && message_data.data.world && !isStateSnapshot) {
    const world_data = {
      ...state.world,
      ...message_data.data.world,
    };
    commit("world_set", world_data);
  }

  if (
    (message_data.type === "cmd.alias.success" ||
      message_data.type === "cmd.unalias.success") &&
    message_data.data &&
    message_data.data.aliases
  ) {
    commit("player_set", { aliases: message_data.data.aliases });
  }

  if (
    message_data.type === "currency.balances_changed" &&
    message_data.data
  ) {
    const walletChange = message_data.data as CurrencyBalancesChangedData;
    if (walletEventMatchesPlayer(state.player, walletChange)) {
      const incomingRevision = Number(walletChange.wallet_revision);
      const rawCurrentRevision = Number(
        state.player?.economy?.wallet_revision ?? -1,
      );
      const currentRevision = Number.isSafeInteger(rawCurrentRevision)
        ? rawCurrentRevision
        : -1;
      if (
        Number.isSafeInteger(incomingRevision) &&
        incomingRevision > currentRevision + 1
      ) {
        if (!state.wallet_sync_requested) {
          commit("wallet_sync_requested_set");
          dispatch("sendWSMessage", {
            type: "cmd.state.sync",
            data: { reason: "wallet_revision_gap" },
          });
        }
      } else {
        commit("player_wallet_changes_apply", walletChange);
      }
    }
  } else if (message_data.type === "cmd.state.sync.error") {
    commit("wallet_sync_requested_clear");
  }

  if (
    message_data.type === TRIGGER_ITEMS_CHANGED_MESSAGE &&
    message_data.data
  ) {
    commit("trigger_items_changed_apply", message_data.data);
  }
  if (
    message_data.type === TRIGGER_MOBS_CHANGED_MESSAGE &&
    message_data.data
  ) {
    commit("trigger_mobs_changed_apply", message_data.data);
  }

  if (
    (MERCHANT_INVENTORY_ADD_MESSAGES.has(message_data.type)
      || MERCHANT_INVENTORY_REMOVE_MESSAGES.has(message_data.type))
    && message_data.data
  ) {
    const item = message_data.data.item;
    if (item) {
      commit("player_inventory_changes_apply", {
        removed: MERCHANT_INVENTORY_REMOVE_MESSAGES.has(message_data.type)
          ? [item]
          : [],
        added: MERCHANT_INVENTORY_ADD_MESSAGES.has(message_data.type)
          ? [item]
          : [],
      });
    }
    if (message_data.data.economy) {
      commit("player_economy_snapshot_apply", message_data.data.economy);
    }
  }

  if (
    message_data.type === "notification.ability.effect" &&
    message_data.data &&
    message_data.data.target &&
    state.player &&
    message_data.data.target.key === state.player.key &&
    Array.isArray(message_data.data.active_effects)
  ) {
    commit("player_active_effects_set", message_data.data.active_effects);
  }

  if (message_data.type === "player.combat_effects.update" && state.player) {
    const playerEffects = playerRoundEffectSnapshot(
      message_data.data?.combatants,
      state.player.key,
    );
    if (playerEffects) {
      const { characterEffects, encounterEffects } = playerEffects;
      commit("player_active_effects_set", characterEffects);
      commit("player_combat_effects_set", encounterEffects);
    } else if (
      message_data.data?.target?.key === state.player.key
      && Array.isArray(message_data.data.active_effects)
    ) {
      commit("player_combat_effects_set", message_data.data.active_effects);
    }
  }

  if (
    message_data.type === "player.combat_effects.update" &&
    Array.isArray(message_data.data?.combatants)
  ) {
    for (const combatant of message_data.data.combatants) {
      commit("combat_target_effects_set", combatant);
    }
  }

  if (
    message_data.type === "cmd.ability.success" &&
    Array.isArray(message_data.data?.prepared_abilities)
  ) {
    commit("prepared_abilities_set", message_data.data.prepared_abilities);
  }

  if (message_data.type === ABILITY_PREPARATIONS_UPDATE_MESSAGE) {
    commit("prepared_abilities_set", message_data.data?.abilities);
  }

  if (ABILITY_PLAYER_STATE_MESSAGES.has(message_data.type)) {
    const abilityActor = message_data.data?.actor;
    if (
      abilityActor
      && state.player
      && abilityActor.key === state.player.key
    ) {
      commit("player_set", abilityActor);
    }
  }

  // Disconection
  if (message_data.type === "system.disconnect.success") {
    if (rootState.auth.user.is_temporary) {
      dispatch("auth/logout", null, { root: true });
      router.push({ name: 'home' });
    } else {
      const world_id = message_data.data.exit_to || state.world.context_id;
      router.push({
        name: 'lobby_world_details',
        params: { world_id: world_id },
      });
    }
    commit("closeWs");
    commit("reset_state");
    return;
  }

  // Instance Transition
  if (message_data.type === "cmd.enter.success" || message_data.type === "cmd.leave.success") {
    if (!message_data.data.banner_url) {
      message_data.data.banner_url = "https://assets.writtenrealms.com/ui/lobby/world-home-bg.jpg";
    }
    // Preload image
    const img = new Image();
    img.src = message_data.data.banner_url;
    commit('transfer_to_set', message_data.data);
  }

  if (message_data.type === 'affect.enter') {
    await new Promise(resolve => setTimeout(resolve, 3000));
    dispatch('cmd', 'enter ' + message_data.data.leader);
  }

  // Successful move updates
  if (
    message_data.type === "cmd.move.success" ||
    message_data.type === "affect.flee.success" ||
    isCompletedFleeSuccess ||
    message_data.type === "notification.transport.exit" ||
    message_data.type === "affect.death" ||
    message_data.type === "affect.transfer"
  ) {
    commit("map_add", message_data.data.room);
    commit("room_set", message_data.data.room);
    if (message_data.data.room && message_data.data.room.key) {
      commit("set_room_key", message_data.data.room.key);
    }
    commit("last_viewed_room_message_set", message_data);
    commit("player_target_set", null);

    // Update focus display
    if (state.player.focus) {
      const focus = state.player.focus.toLowerCase();
      const char = message_data.data.room.chars.find((char) => char.keywords.includes(focus));
      if (char) {
        commit("update_focus_data", char);
      } else {
        commit("update_focus_data", {});
      }
    }

  } else if (message_data.type === "cmd./jump.success") {
    commit("map_add", message_data.data.target);
    commit("room_set", message_data.data.target);
    if (message_data.data.target && message_data.data.target.key) {
      commit("set_room_key", message_data.data.target.key);
    }
    commit("player_target_set", null);
  }

  // Door transitions carry compact deltas so the map and active room can be
  // updated without replacing either room snapshot. This covers movement,
  // player commands, privileged slash commands, and room notifications.
  if (Array.isArray(message_data.data?.door_states)) {
    for (const data of message_data.data.door_states) {
      commit("map_update_door_state", {
        room_key: data.key,
        direction: data.direction,
        door_state: data.door_state,
      });
    }
  }

  // Move in notifications
  if (
    message_data.type === "notification.movement.enter" ||
    message_data.type === "notification.cmd.flee.enter" ||
    message_data.type === "notification./transfer.enter" ||
    message_data.type === "notification./jump.enter"
  ) {
    commit("room_chars_add", message_data.data.actor);
  }

  // Move out notifications
  if (
    message_data.type === "notification.movement.exit" ||
    message_data.type === "notification.cmd.flee.exit" ||
    message_data.type === "notification./transfer.exit" ||
    message_data.type === "notification./jump.exit"
  ) {
    commit("room_chars_remove", message_data.data.actor);

    if (state.player_target &&
        state.player_target.key === message_data.data.actor.key) {
      commit("player_target_set", null);
    }
  }

  // Update room chars on attack for state reasons
  if (message_data.type === "notification.combat.attack") {
    commit("room_chars_update", message_data.data.actor);
    commit("room_chars_update", message_data.data.target);

    // Update target data if it's the player's target
    if (state.player_target && state.player_target.key === message_data.data.actor.key) {
      commit("player_target_set", message_data.data.actor);
    }
    if (state.player_target && state.player_target.key === message_data.data.target.key) {
      commit("player_target_set", message_data.data.target);
    }

    if (state.player.focus) {
      const focus = state.player.focus.toLowerCase();
      if (message_data.data.actor.keywords && message_data.data.actor.keywords.includes(focus)) {
        commit("update_focus_data", message_data.data.actor);
      } else if (message_data.data.target.keywords && message_data.data.target.keywords.includes(focus)) {
        commit("update_focus_data", message_data.data.target);
      }
    }
  }

  if (isCombatDisengage && message_data.data) {
    if (message_data.data.actor) {
      commit("room_chars_update", message_data.data.actor);
    }
    if (message_data.data.target) {
      commit("room_chars_update", message_data.data.target);
    }
    if (message_data.data.next_target) {
      commit("room_chars_update", message_data.data.next_target);
    }
  }

  // Open & close messages
  if (
    message_data.type === "door.open" ||
    message_data.type === "door.close" ||
    message_data.type === "notification.door.open" ||
    message_data.type === "notification.door.close" ||
    message_data.type === "notification.door.reset"
  ) {
    commit("map_add", message_data.data.room);
    if (message_data.data.exit_room) {
      commit("map_add", message_data.data.exit_room);
    }
  }

  // Anything that has an actor who is the connected player
  if (
    !ABILITY_PLAYER_STATE_MESSAGES.has(message_data.type) &&
    message_data.type !== TRIGGER_ITEMS_CHANGED_MESSAGE &&
    message_data.type !== TRIGGER_MOBS_CHANGED_MESSAGE &&
    message_data.data["actor"] &&
    state.player &&
    message_data.data["actor"].key === state.player.key
  ) {
    commit("player_set", message_data.data.actor);

    const payload_room_key =
      (message_data.data.room && message_data.data.room.key) ||
      ((message_data.data.target_type === "room" && message_data.data.target)
        ? message_data.data.target.key
        : null);
    if (payload_room_key) {
      commit("set_room_key", payload_room_key);
    } else if (message_data.data.actor && message_data.data.actor.room) {
      commit("set_room_key", message_data.data.actor.room.key);
    }
  }

  // Inventory affect
  if (message_data.type === "affect.inventory.remove") {
    commit("player_remove_from_inventory", message_data.data.items);
  }

  // Room updating on look
  if (
    message_data.type === "cmd./jump.success" ||
    (message_data.type === "cmd.look.success" &&
      message_data.data.target_type === "room")
  ) {
    commit("room_set", message_data.data.target);
    commit("map_add", message_data.data.target);
    if (message_data.data.target && message_data.data.target.key) {
      commit("set_room_key", message_data.data.target.key);
    }
    commit("last_viewed_room_message_set", message_data);

    if (state.player.focus) {
      const focus = state.player.focus.toLowerCase();
      const char = message_data.data.target.chars.find((char) => char.keywords.includes(focus));
      if (char) {
        commit("update_focus_data", char);
      } else {
        commit("update_focus_data", {});
      }
    }

  }

  // Keep room inventory/chars current after item manipulation commands.
  if (
    (message_data.type === "cmd.get.success" ||
      message_data.type === "cmd.put.success" ||
      message_data.type === "cmd.drop.success" ||
      message_data.type === "cmd./purge.success" ||
      message_data.type === "cmd./regen.success" ||
      message_data.type === "cmd./set.success" ||
      message_data.type === "cmd./setclass.success") &&
    message_data.data &&
    message_data.data.room
  ) {
    commit("room_set", message_data.data.room);
    if (message_data.data.room.key) {
      commit("set_room_key", message_data.data.room.key);
    }
  }

  // On death, clear out combat window
  if (message_data.type === "affect.death") {
    commit("player_target_set", null);
  }

  // Track effects for all chars
  if (message_data.type === "effect.start") {
    commit("effects_add", message_data.data);
    setTimeout(() => {
      commit("effects_remove", message_data.data);
    }, message_data.data.duration * 1000);

    if (message_data.data.target === state.player.key) {
      commit("player_effects_add", message_data.data);
    }
  }

  // Effects expiration
  if (message_data.type === "effect.end") {
    if (message_data.data.target === state.player.key) {
      commit("player_effects_remove", message_data.data);
      commit("effects_remove", message_data.data);
    }
  }

  // Special case of effects expiration: anathema & combust
  if (message_data.type === "notification.combat.attack") {
    if (message_data.data.attack === "combust") {
      commit("effects_consume", {
        actor_key: message_data.data.actor.key,
        target_key: message_data.data.target.key,
        effect_code: "burn",
      });
    } else if (message_data.data.attack === "anathema") {
      commit("effects_consume", {
        actor_key: message_data.data.actor.key,
        target_key: message_data.data.target.key,
        effect_code: "wrack",
      });
    }
  }

  // Dispel / purge / purify
  if (message_data.type === "effect.start") {

    let remove_effects = [];

    if (message_data.data.code === 'purge' || message_data.data.code === 'purify') {
      remove_effects = message_data.data.removed_effects;
    }

    if (remove_effects.length) {
      // for each effect code to remove, commit effects_consume
      for (const effect_code of remove_effects) {
        commit("effects_consume", {
          actor_key: message_data.data.actor.key,
          target_key: message_data.data.target,
          effect_code: effect_code,
        });
      }
    }
  }

  // Hint processing
  if (
    (
      message_data.type === "cmd.move.success" ||
      message_data.type === "affect.transfer" ||
      isStateSnapshot
    ) &&
    message_data.data.room.hint
  ) {
    commit("hint_set", message_data.data.room.hint);
  } else if (
    message_data.type === "cmd.look.success" &&
    message_data.data.target.hint
  ) {
    commit("hint_set", message_data.data.target.hint);
  }
  if (
    message_data.type === "room_write" &&
    message_data.text ===
      "Wincing in pain, you try to familiarize yourself with your surroundings."
  ) {
    commit("hint_set", "2:Enter 'look' or 'l'");
  }

  // Resets
  if (
    message_data.type === "cmd./reset.success" &&
    (!message_data.data || message_data.data.reset_scope !== "instance")
  ) {
    commit("full_screen_message_set", "Resetting...");
    dispatch("request_enter_world", {
      player_id: state.player.id,
      world_id: state.world.id,
    });
  }

  if (message_data.type === "notification.death") {
    commit("last_death_set", message_data.data.deceased.key);
    commit("room_chars_remove", message_data.data.deceased);
    if (message_data.data.room) {
      commit("room_set", message_data.data.room);
      commit("map_add", message_data.data.room);
      if (message_data.data.room.key) {
        commit("set_room_key", message_data.data.room.key);
      }
    }
    if (message_data.data.killer) {
      commit('room_chars_update_target', {
        char: message_data.data.killer,
        target: null,
      });
      if (state.player && message_data.data.killer.key === state.player.key) {
        commit("player_target_set", null);
      }
    }
  }

  // Complete
  if (message_data.type === "cmd.completeworld.success") {
    commit("ui/notification_clear", null, { root: true })

    if (rootState.auth.user.is_temporary) {
      router.push({
        name: "lobby_world_complete_signup",
        params: {
          player_id: state.player.id,
          world_id: state.world.id,
        },
      });
    } else {
      router.push({
        name: "lobby_world_transfer",
        params: {
          player_id: state.player.id,
          world_id: state.world.id,
        },
      });
    }
  }

  // // Enter Instance
  // if (message_data.type === 'cmd.enter.success') {
  //   commit("full_screen_message_set", "Entering Instance...")
  //   dispatch("world_enter", {
  //     player_id: state.player.id,
  //   })
  // }

  // // Exit Instance
  // if (message_data.type === 'cmd.leave.success') {
  //   commit("full_screen_message_set", "Exiting Instance...")
  //   dispatch("world_enter", {
  //     player_id: state.player.id,
  //   })
  // }

  // Who list update
  if (message_data.type === "notification.who") {
    commit("who_list_set", message_data.data);
  }

  // Focus update
  if (message_data.type === "cmd.focus.success") {
    commit("player_focus_set", message_data.data.focus);
  }

  // Target setting
  if (message_data.type === "cmd.kill.success") {
    // If we're in an initial hit, the backend takes care of setting actor.target
    // for that message (but there's a delay afterwards). It would be better if it
    // set data.target instead, but as a band-aid we're taking advantage of this.
    commit("player_target_set", message_data.data.actor.target);
  } else if (message_data.type === "notification.combat.attack") {
    // We only set the player target if it's a player auto-attacking, essentially.
    // And we look at the player's target, rather than setting it when they're
    // the recipient of one, since he could be attacked by multiple enemies.
    if (message_data.data.actor.key === state.player.key
    && message_data.data.target.key != state.player.key) {
      commit("player_target_set", message_data.data.target);
    }

    // Since player_set does a partial update if that's all it can do,
    // we're taking advantage of the fact that the state player could be
    // the target of someone else's ability (especially if it's a cast outside of
    // auto-attack rounds).
    if (message_data.data.target.key == state.player.key) {
      commit("player_set", message_data.data.target);
    }

  } else if (message_data.type === "cmd.disengage.success") {
    // Clear the old target first so its effect state is not carried onto a
    // remaining encounter's new primary target by player_target_set.
    commit("player_target_set", null);
    commit("player_target_set", message_data.data?.actor?.target || null);
  } else if (message_data.type === "affect.flee.success" || isCompletedFleeSuccess) {
    commit("player_target_set", null);
  } else if (message_data.type === "notification.duel.completed") {
    commit("player_target_set", null);
    if (message_data.data.actor) {
      commit("room_chars_update", message_data.data.actor);
    }
    if (message_data.data.target) {
      commit("room_chars_update", message_data.data.target);
    }
  }

  if (message_data.type === 'affect.delete') {
    if (state.transfer_to.transfer_to_world_id) {
      setTimeout(() => {
        dispatch("request_enter_world", {
          player_id: state.player.id,
          world_id: state.transfer_to.transfer_to_world_id,
          instance_ref: message_data.data.instance_ref,
        });
      }, 2000);
    }
  };
};

const actions = {

  request_enter_world: async ({ commit, dispatch }, { player_id, world_id }) => {
    commit("reset_state");
    commit(
      "ui/notification_set",
      { text: "Entering world...", expires: false },
      { root: true });

    await dispatch('forge/send', {
      type: 'job',
      job: 'enter_world',
      player_id,
      world_id,
    }, { root: true });
  },

  enter_ready_world: async ({ commit, dispatch }, { player_id, player_config, world, ws_uri, motd }) => {
    commit("reset_state");
    commit("ws_uri_set", ws_uri);
    commit("world_set", world);
    commit("player_config_set", player_config);
    commit("pregame_set", { player_id: player_id });
    commit("motd_set", motd);
    dispatch("openWebSocket");
  },

  world_enter: async ({ commit, dispatch }, { player_id }) => {
    commit("reset_state");
    commit(
      "ui/notification_set",
      { text: "Entering world...", expires: false },
      { root: true }
    );
    try {
      const resp = await axios.post(`/game/enter/`, {
        player_key: `player.${player_id}`,
      });
      commit("world_set", resp.data.world);
      commit("player_config_set", resp.data.player_config);
      if (resp.data.cluster_id) {
        commit("ws_uri_set", `ws://localhost/websocket/${resp.data.cluster_id}/cmd`);
      }

      commit("pregame_set", {
        player_id: player_id,
      });
      dispatch("openWebSocket");
    } catch (e: any) {
      let error_message = "Unable to enter world.";
      if (
        e.response.status === 400 &&
        e.response.data &&
        e.response.data.length
      ) {
        error_message = e.response.data[0];
      }
      commit("ui/notification_set_error", error_message, {
        root: true,
      });
    }
  },

  world_exited: async ({ commit, dispatch, state, rootState }, data) => {
    console.log("world_exited, state: ", state);
    if (rootState.auth.user.is_temporary) {
      dispatch("auth/logout", null, { root: true });
      router.push({ name: 'home'})
    } else if (state.transfer_to.transfer_to_world_id) {
      // Exiting the main world to go into an instance, don't actually go back to the lobby
      console.log('requesting entrance to ', state.transfer_to.transfer_to_world_id);
      return;
    } else {
      const world_id = data.exit_to || (state.world && state.world.context_id);
      if (world_id &&
          (router.currentRoute.value.name !== 'lobby_world_details'
            || router.currentRoute.value.params.world_id !== world_id)) {
        router.push({
          name: 'lobby_world_details',
          params: { world_id: world_id },
        });
      }
    }
    commit("closeWs");
    commit("reset_state");
  },

  openWebSocket: async ({ commit, rootState, state, dispatch }) => {
    const onopen = () => {
      dispatch("sendWSMessage", {
        type: "system.connect",
        data: { player_key: "player." + state.player_id },
      });
    };

    const onmessage = (event) => {
      receiveMessage({ event, rootState, state, dispatch, commit });
    };

    const onerror = (error) => {
      console.error('WebSocket Error:', error);
      cancelAllEditRoomTabs();
      commit("command_receipts_unconfirm_pending");
    };

    const onclose = () => {
      cancelAllEditRoomTabs();
      commit("connected_clear");
      commit("command_receipts_unconfirm_pending");
    };

    commit("openWS", { onopen, onmessage, onerror, onclose });
  },

  sendWSMessage: async ({ rootState, state }, payload) => {
    console.log(`SEND ${payload.type}`);
    console.log(payload);
    const websocket = state.websocket;
    if (!websocket || websocket.readyState !== 1) {
      return false;
    }
    try {
      websocket.send(JSON.stringify({
        ...payload,
        token: rootState.auth.token,
      }));
      return true;
    } catch (error) {
      console.error("Unable to send WebSocket message:", error);
      return false;
    }
  },

  cmd: async ({ dispatch, state, commit }, cmd) => {
    // Process a string command as entered by a user.

    // If, rather than passing a string, we pass an object, the
    // cmd is assumed to be in object.cmd, and then a number of
    // options are parsed in the rest of the attributes:
    // - silent: whether to actually echo back the command or not
    let silent = false;
    if (typeof cmd == "object") {
      const payload = { ...cmd };
      cmd = payload.cmd;
      if (payload.silent) {
        silent = true;
      }
    }

    const lcmd = cmd.toLowerCase();
    const lfirst_token = lcmd.split(" ")[0];
    const isHistoryReplay = /^!\d+$/.test(cmd.trim());
    const isAliasCommand = lfirst_token === "alias" || lfirst_token === "unalias";
    const playerAliases = state.player?.aliases || {};
    const hasKnownAlias = !isAliasCommand && cmd
      .split(";")
      .some((segment) => {
        const firstToken = segment.trim().split(/\s+/)[0]?.toLowerCase();
        return !!(firstToken && playerAliases[firstToken]);
      });

    // Special focus processing
    if (
      state.player.focus &&
      (lcmd === "k" || lcmd === "ki" || lcmd === "kil" || lcmd === "kill")
    ) {
      // Kill with no arguments
      cmd = `${cmd} ${state.player.focus}`;
    } else if (!new RegExp("^" + lfirst_token).exec("help")) {
      // exclude 'help' from focus processing
      // F commands
      const cmd_tokens = cmd.split(" ");
      if (cmd_tokens.length === 2) {
        const arg = cmd_tokens[1];
        if (new RegExp("^" + arg).exec("focus")) {
          cmd = `${cmd_tokens[0]} ${state.player.focus}`;
        }
      }
    }

    const resolutionKind = isHistoryReplay
      ? "history"
      : (hasKnownAlias ? "alias" : null);
    const shouldEcho = !silent;

    // Quit is a connection operation, not a queued game command. Keep its
    // existing echo without starting a receipt timer that cannot be fulfilled.
    if (cmd.toLowerCase() === "quit") {
      if (shouldEcho) {
        commit("message_add", { type: "cmd.text", text: cmd, echo: true });
      }
      dispatch("sendWSMessage", { type: "system.disconnect" });
      commit("full_screen_message_set", "Disconnecting...");
      return;
    }

    const requestId = createCommandRequestId();
    if (state.player?.is_builder) {
      prepareEditRoomTab(requestId, cmd);
    }
    const wireMessage = {
      type: "cmd.text",
      text: cmd,
      echo: true,
      request_id: requestId,
    };

    if (shouldEcho) {
      commit("message_add", {
        ...wireMessage,
        command_receipt: initialCommandReceipt(),
        ...(resolutionKind ? {
          command_resolution: {
            kind: resolutionKind,
            original_text: cmd.trim(),
            resolved: false,
          },
        } : {}),
      });
      scheduleCommandReceiptTransition(
        commit,
        requestId,
        "unconfirmed",
        COMMAND_RECEIPT_TIMEOUT_MS,
        { phase: "unconfirmed" },
      );
    }

    const sent = await dispatch("sendWSMessage", wireMessage);
    if (!sent) {
      cancelEditRoomTab(requestId);
    }
    if (!sent && shouldEcho) {
      clearCommandReceiptTimers(requestId);
      commit("command_receipt_update", {
        request_id: requestId,
        phase: "unconfirmed",
      });
    }
    if (state.hint) {
      commit("hint_clear");
    }

    commit("lookup_clear");
  },

  cmd_structured: async ({ dispatch, commit, state }, payload) => {
    const requestId = createCommandRequestId();
    const wirePayload = {
      ...payload,
      echo: true,
      request_id: requestId,
    };
    commit("message_add", {
      ...wirePayload,
      command_receipt: initialCommandReceipt(),
    });
    scheduleCommandReceiptTransition(
      commit,
      requestId,
      "unconfirmed",
      COMMAND_RECEIPT_TIMEOUT_MS,
      { phase: "unconfirmed" },
    );
    const sent = await dispatch("sendWSMessage", wirePayload);
    if (!sent) {
      clearCommandReceiptTimers(requestId);
      commit("command_receipt_update", {
        request_id: requestId,
        phase: "unconfirmed",
      });
    }
    if (state.hint) {
      commit("hint_clear");
    }
  },

  play: async ({ commit, dispatch }) => {
    try {
      const resp = await axios.post("/game/play/");
      commit("auth/auth_set_tokens", {
        access: resp.data.access || resp.data.token,
        refresh: resp.data.refresh
      }, { root: true });
      commit("auth/user_set", resp.data.user, { root: true });
      const player_id = resp.data.player.id;
      dispatch("request_enter_world", {
        player_id,
        world_id: resp.data.world_id
      });
    } catch (e: any) {
      // If it's a 400, show the error message to the user
      if (e.response.status === 400) {
        commit("ui/modal_clear", null, { root: true });
        commit("ui/notification_set_error", e.response.data[0], { root: true });
        return;
      }
    }
  },

  save_player_config: async ({ commit, state }, config) => {
    state.player_id;
    await axios.post("/game/player/config/", config, {
      headers: { "X-PLAYER-ID": state.player.id },
    });
    commit("player_config_set", config);
  },
};

function uuidv4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function(c) {
    let r = (Math.random() * 16) | 0,
      v = c == "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const combatRoundGroup = (message) => {
  const roundId = message?.data?.round_id;
  if (!roundId) return null;

  const encounterRound = String(roundId).match(/^encounter:[^:]+:(\d+)$/);
  if (encounterRound) {
    return `combat-round:${encounterRound[1]}`;
  }

  return roundId;
};

const mutations = {
  message_add: (state, message) => {
    // Console entries are terminal-style snapshots. Never retain references
    // to the live payload objects that the rest of the store may update.
    const historyMessage = _.cloneDeep(message);
    historyMessage.receive_ts = new Date().getTime();
    historyMessage.message_id = uuidv4();
    if (!historyMessage.group) {
      const group = combatRoundGroup(historyMessage);
      if (group) {
        historyMessage.group = group;
      }
    }

    state.messages.push(historyMessage);
    const messages_length = state.messages.length;
    if (messages_length > MESSAGE_LIMIT) {
      const removedMessages = state.messages.slice(
        0,
        messages_length - MESSAGE_LIMIT,
      );
      for (const removedMessage of removedMessages) {
        if (removedMessage.request_id) {
          clearCommandReceiptTimers(removedMessage.request_id);
        }
      }
      state.messages = state.messages.slice(
        messages_length - MESSAGE_LIMIT,
        messages_length
      );
    }
  },

  command_receipt_update: (state, payload) => {
    const requestId = payload?.request_id;
    if (!requestId) return;
    for (let index = state.messages.length - 1; index >= 0; index -= 1) {
      const message = state.messages[index];
      if (
        message.echo &&
        message.request_id === requestId &&
        message.command_receipt
      ) {
        message.command_receipt = transitionCommandReceipt(
          message.command_receipt,
          payload,
        );
        return;
      }
    }
  },

  command_echo_resolve: (state, payload) => {
    const message = state.messages[payload?.index];
    const resolution = payload?.resolution;
    const resolutionData = commandResolution(resolution);
    if (
      !message?.echo ||
      !resolution?.text ||
      !resolutionData
    ) {
      return;
    }
    message.type = resolution.type;
    message.text = resolution.text;
    message.data = _.cloneDeep(resolution.data || {});
    message.command_resolution = {
      ...(message.command_resolution || {}),
      kind: resolutionData.kind,
      original_text: resolutionData.originalText,
      resolved: true,
      resolved_at: Date.now(),
    };
    if (message.command_receipt) {
      message.command_receipt = transitionCommandReceipt(
        message.command_receipt,
        { phase: "received" },
      );
    }
  },

  command_receipts_unconfirm_pending: (state) => {
    clearCommandReceiptTimers();
    for (const message of state.messages) {
      if (
        message.command_receipt?.phase === "sending" ||
        message.command_receipt?.phase === "received" ||
        (
          message.command_receipt?.phase === "accepted" &&
          !message.command_receipt.compact
        )
      ) {
        message.command_receipt = transitionCommandReceipt(
          message.command_receipt,
          {
            phase: "unconfirmed",
            message: "The command outcome could not be confirmed because the connection closed.",
          },
        );
      }
    }
  },

  event_id_seen: (state, eventId) => {
    if (!eventId || state.received_event_ids[eventId]) return;
    state.received_event_ids[eventId] = true;
    state.received_event_id_order.push(eventId);
    if (state.received_event_id_order.length > MESSAGE_LIMIT * 3) {
      const expiredEventId = state.received_event_id_order.shift();
      if (expiredEventId) {
        delete state.received_event_ids[expiredEventId];
      }
    }
  },

  last_viewed_room_message_set: (state, message) => {
    const latestHistoryMessage = state.messages[state.messages.length - 1];
    state.last_viewed_room_message = (
      latestHistoryMessage?.type === message?.type
        ? latestHistoryMessage
        : _.cloneDeep(message)
    );
  },

  last_message_set: (state, message) => {
    const latestHistoryMessage = state.messages[state.messages.length - 1];
    state.last_message[message.type] = (
      latestHistoryMessage?.type === message?.type
        ? latestHistoryMessage
        : message
    );
  },

  messages_clear: (state) => {
    clearCommandReceiptTimers();
    state.messages = [];
    state.received_event_ids = {};
    state.received_event_id_order = [];
  },

  ws_uri_set: (state, uri) => {
    state.uri = uri;
  },

  openWS: (state, { onopen, onmessage, onerror, onclose }) => {
    state.websocket = new WebSocket(state.uri);
    state.websocket.onopen = onopen;
    state.websocket.onmessage = onmessage;
    state.websocket.onerror = onerror;
    state.websocket.onclose = onclose;
  },

  closeWs: (state) => {
    if (state.websocket) {
      state.websocket.close();
    }
  },

  player_set: (state, player) => {
    const nextPlayer = playerWithCurrentEconomyWhenNewer(state.player, player);
    state.player = {
      ...state.player,
      ...nextPlayer,
    };

    if (player.stance && player.stance != state.player_stance) {
      state.player_stance = player.stance;
    }

    if (player.archetype && player.archetype != state.player_archetype) {
      state.player_archetype = player.archetype;
    }

    if (player.level && player.level != state.player_level) {
      state.player_level = player.level;
    }
  },

  prepared_abilities_set: (state, abilities) => {
    state.prepared_abilities = Array.isArray(abilities)
      ? [...new Set(
          abilities
            .filter(ability => typeof ability === "string")
            .map(ability => ability.trim().toLowerCase())
            .filter(Boolean),
        )]
      : [];
  },

  player_wallet_changes_apply: (state, payload: CurrencyBalancesChangedData) => {
    if (!state.player || !payload) return;
    if (!walletEventMatchesPlayer(state.player, payload)) return;

    const walletRevision = Number(payload.wallet_revision);
    if (!Number.isSafeInteger(walletRevision) || walletRevision < 0) return;

    const currentEconomy = state.player.economy || {
      wallet_revision: -1,
      balances: {},
    };
    const currentRevision = Number(currentEconomy.wallet_revision ?? -1);
    if (walletRevision <= currentRevision) return;

    const balances = { ...(currentEconomy.balances || {}) };
    for (const change of payload.changes || []) {
      const currency = change?.currency;
      const code = typeof currency === "string" ? currency : currency?.code;
      if (!code || typeof change?.after !== "number") continue;
      balances[code] = change.after;
    }

    state.player = {
      ...state.player,
      economy: {
        ...currentEconomy,
        wallet_revision: walletRevision,
        balances,
      },
    };
  },

  wallet_sync_requested_set: (state) => {
    state.wallet_sync_requested = true;
  },

  wallet_sync_requested_clear: (state) => {
    state.wallet_sync_requested = false;
  },

  player_active_effects_set: (state, active_effects) => {
    if (!state.player) return;
    state.player = {
      ...state.player,
      active_effects: Array.isArray(active_effects) ? active_effects : [],
    };
  },

  player_combat_effects_set: (state, active_effects) => {
    if (!state.player) return;
    state.player = {
      ...state.player,
      combat_effects: Array.isArray(active_effects) ? active_effects : [],
    };
  },

  combat_target_effects_set: (state, payload) => {
    if (
      !state.player_target ||
      !payload?.target?.key ||
      state.player_target.key !== payload.target.key
    ) return;
    state.player_target = {
      ...state.player_target,
      active_effects: Array.isArray(payload.active_effects)
        ? payload.active_effects
        : [],
    };
  },

  player_remove_from_inventory: (state, items) => {
    const inv = _.differenceWith(state.player.inventory, items, (a, b) => {
      return a.key == b.key;
    });
    // Vue.set(state.player, "inventory", inv);
    state.player['inventory'] = inv;
  },

  player_inventory_changes_apply: (state, payload) => {
    if (!Array.isArray(state.player?.inventory)) return;
    applyItemChangesInPlace(
      state.player.inventory,
      payload?.removed,
      payload?.added,
    );
  },

  player_economy_snapshot_apply: (state, payload) => {
    if (!state.player || !payload) return;
    const incomingRevision = Number(payload.wallet_revision);
    const currentEconomy = state.player.economy || {
      wallet_revision: -1,
      balances: {},
    };
    const currentRevision = Number(currentEconomy.wallet_revision ?? -1);
    if (
      !Number.isSafeInteger(incomingRevision)
      || incomingRevision < currentRevision
    ) {
      return;
    }
    state.player = {
      ...state.player,
      economy: {
        ...currentEconomy,
        ...payload,
        balances: {
          ...(currentEconomy.balances || {}),
          ...(payload.balances || {}),
        },
      },
    };
  },

  trigger_items_changed_apply: (state, payload) => {
    if (!payload) return;

    const eventRoomKey = String(payload.room?.key ?? "");
    const currentRoomKey = String(state.room?.key ?? "");
    if (eventRoomKey && currentRoomKey === eventRoomKey) {
      if (Array.isArray(state.room?.inventory)) {
        applyItemChangesInPlace(
          state.room.inventory,
          payload.room_items_removed,
          payload.room_items_added,
        );
      }
    }

    if (
      state.player &&
      String(payload.actor?.key ?? "") === String(state.player.key ?? "") &&
      Array.isArray(state.player.inventory)
    ) {
      applyItemChangesInPlace(
        state.player.inventory,
        payload.actor_inventory_removed,
        payload.actor_inventory_added,
      );
    }
  },

  trigger_mobs_changed_apply: (state, payload) => {
    if (!payload || !Array.isArray(payload.mobs)) return;

    const eventRoomKey = String(payload.room?.key ?? "");
    const currentRoomKey = String(state.room?.key ?? "");
    if (!eventRoomKey || currentRoomKey !== eventRoomKey) return;

    replaceRoomChars(
      state,
      applyRoomCharChanges(state.room_chars, payload.mobs),
    );

    if (state.player_target) {
      [state.player_target] = applyRoomCharChanges(
        [state.player_target],
        payload.mobs,
      );
    }
    if (state.focus_data?.key) {
      [state.focus_data] = applyRoomCharChanges(
        [state.focus_data],
        payload.mobs,
      );
    }
  },

  player_focus_set: (state, focus) => {
    state.player = {
      ...state.player,
      focus: focus,
    };
  },

  player_effects_add: (state, effect) => {
    effect.start = new Date().getTime();
    if (!state.player_effects.length) {
      state.player_effects = [effect];
      return;
    }

    // Filter out the existing effects effects of the same
    // actor & code combination
    const applied_effects = _.filter(
      state.player_effects,
      (existing_effect) => {
        return (
          existing_effect.code !== effect.code ||
          existing_effect.actor !== effect.actor
        );
      }
    );

    applied_effects.push(effect);
    state.player_effects = applied_effects;
  },

  player_effects_remove: (state, effect) => {
    const kept_effects: {}[] = [];
    for (const existing_effect of state.player_effects) {
      if (existing_effect["expires"] != effect["expires"]) {
        kept_effects.push(existing_effect);
      }
    }
    state.player_effects = kept_effects;
  },

  player_effects_clear: (state) => {
    state.player_effects = [];
  },

  effects_add: (state, effect) => {
    effect.start = new Date().getTime();
    const char_effects = state.effects[effect.target] || [];

    if (!char_effects.length) {
      char_effects.push(effect);
      // Vue.set(state.effects, effect.target, char_effects);
      state.effects[effect.target] = char_effects;
      return;
    }

    let applied_effects: any[] = [];
    if (effect.allow_multiple) {
      applied_effects = char_effects;
    } else {
      applied_effects = _.filter(char_effects, (existing_effect) => {
        return (
          existing_effect.code !== effect.code ||
          existing_effect.actor !== effect.actor
        );
      });
    }
    applied_effects.push(effect);

    // Vue.set(state.effects, effect.target, applied_effects);
    state.effects[effect.target] = applied_effects;
  },

  effects_remove: (state, effect) => {
    const char_effects = state.effects[effect.target] || [];
    if (!char_effects.length) return;

    const applied_effects: {}[] = [];
    for (const existing_effect of char_effects) {
      if (effect.key !== existing_effect.key) {
        applied_effects.push(existing_effect);
      }
    }

    // Vue.set(state.effects, effect.target, applied_effects);
    state.effects[effect.target] = applied_effects;
  },

  effects_consume: (state, { actor_key, target_key, effect_code }) => {
    const char_effects = state.effects[target_key];
    if (!char_effects || !char_effects.length) return;

    const kept_effects: {}[] = _.filter(char_effects, (effect) => {
      return (
        effect.actor != actor_key &&
        effect.target != target_key &&
        effect.code != effect_code
      );
    });
    // Vue.set(state.effects, target_key, kept_effects);
    state.effects[target_key] = kept_effects;
  },

  player_config_set: (state, player_config) => {
    state.player_config = player_config;
  },

  set_map: (state, map) => {
    state.map = map;
  },
  map_add: (state, room) => {
    state.map = {
      ...state.map,
      [room.key]: room,
    };
  },
  map_update_door_state: (state, { room_key, direction, door_state }) => {
    if (!room_key || !direction || !door_state) return;
    const state_key = `${direction}_door_state`;
    const map_room = state.map && state.map[room_key];
    if (map_room && map_room[state_key] !== door_state) {
      state.map = {
        ...state.map,
        [room_key]: {
          ...map_room,
          [state_key]: door_state,
        },
      };
    }
    if (
      state.room &&
      state.room.key === room_key &&
      state.room[state_key] !== door_state
    ) {
      state.room = {
        ...state.room,
        [state_key]: door_state,
      };
    }
  },
  set_room_key: (state, room_key) => {
    state.room_key = room_key;
  },
  // mutation called after a successful game entry from the API
  // side, but before the Websocket side.
  pregame_set: (state, { player_id }) => {
    state.player_id = player_id;
    //state.world_id = world_id;
  },
  world_set: (state, world) => {
    state.world = world;
    state.factions = world.factions;
  },

  width_set: (state, width) => {
    state.width = width;
    if (width > 768) {
      state.is_mobile = false;
    } else {
      state.is_mobile = true;
    }
  },

  room_set: (state, room) => {
    const chars = (room.chars || []).map(cloneRoomChar);
    state.room = { ...room, chars };
    state.room_chars = chars;
  },

  room_chars_add: (state, char) => {
    const chars = state.room_chars.map(cloneRoomChar);
    const incoming = cloneRoomChar(char);
    const existingIndex = incoming.key
      ? chars.findIndex(existingChar => existingChar.key === incoming.key)
      : -1;
    if (existingIndex === -1) {
      chars.push(incoming);
    } else {
      chars[existingIndex] = incoming;
    }
    replaceRoomChars(state, chars);
  },

  room_chars_update: (state, char) => {
    const room_chars: {}[] = [];
    for (const existing_char of state.room_chars) {
      if (existing_char.key === char.key) {
        room_chars.push(cloneRoomChar(char));
      } else {
        room_chars.push(existing_char);
      }
    }
    replaceRoomChars(state, room_chars);
  },

  room_chars_update_target: (state, { char, target }) => {
    const room_chars = state.room_chars.map(c => {
      if (c.key === char.key) {
        return { ...c, target };
      }
      return c;
    });
    replaceRoomChars(state, room_chars);
  },

  room_chars_remove: (state, char) => {
    const room_chars: {}[] = [];
    for (const existing_char of state.room_chars) {
      if (existing_char.key != char.key) {
        room_chars.push(existing_char);
      }
    }
    replaceRoomChars(state, room_chars);
  },

  connected_set: (state) => {
    state.is_connected = true;
  },

  connected_clear: (state) => {
    state.is_connected = false;
  },

  lookup_set: (state, lookup) => {
    state.lookup = lookup;
  },

  lookup_clear: (state) => {
    state.lookup = null;
  },

  hint_set: (state, hint) => {
    state.hint = hint;
  },

  hint_clear: (state) => {
    state.hint = null;
  },

  full_screen_message_set: (state, message) => {
    state.full_screen_message = message;
  },

  full_screen_message_clear: (state) => {
    state.full_screen_message = "";
  },

  transfer_to_set: (state, data) => {
    state.transfer_to = data;
  },

  reset_state: (state) => {
    clearCommandReceiptTimers();
    cancelAllEditRoomTabs();
    const new_state = set_initial_state();
    for (const attr in new_state) {
      state[attr] = new_state[attr];
    }
  },

  last_death_set: (state, key) => {
    state.last_death = key;
  },

  last_death_clear: (state) => {
    state.last_death = null;
  },

  started_casts_add: (state, { expires }) => {
    // Vue.set(state.started_casts, expires, true);
    state.started_casts[expires] = true;
  },

  hover_entity_set: (state, entity) => {
    state.hover_entity = entity;
  },

  popup_hover_set: (state, value: true | false) => {
    state.popup_hover = value;
  },

  player_target_set: (state, target) => {
    // if (target.key != state.player.key) {
    const existingEffects = state.player_target?.key === target?.key
      && Array.isArray(state.player_target?.active_effects)
      ? state.player_target.active_effects
      : null;
    state.player_target = target && existingEffects && !Array.isArray(target.active_effects)
      ? { ...target, active_effects: existingEffects }
      : target;
    // }
  },

  who_list_set: (state, who_list) => {
    state.who_list = who_list;
  },

  com_list_add: (state, com) => {
    state.com_list.push(com);
  },

  motd_set: (state, motd) => {
    state.motd = motd;
  },

  update_focus_data: (state, data) => {
    state.focus_data = data;
  }
};

const getters = {
  consoleMessages: (state) => {
    return _.filter(state.messages, (message) => {
      // Exclude chat messages option
      if (!state.player_config.display_chat && message.type == 'notification.cmd.chat.success') {
        return false;
      }
      /*
      // Exclude join / part messages option
      if (!state.player_config.display_connect
        && (
          message.type === 'notification.connect.success'
          || message.type === 'notification.system.disconnect.success')) {
        return false;
      }
      */

      return (
        message.text ||
        message.type === "cmd.state.sync.success" ||
        (message.type === "system.connect.success" && message.data && message.data.room)
      );
    });
  },
};

export default {
  namespaced: true,
  state: set_initial_state(),
  actions,
  mutations,
  getters,
};
