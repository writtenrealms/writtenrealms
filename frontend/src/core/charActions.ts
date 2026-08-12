export const getCoreFaction = (entity) => {
  if (!entity) return "";
  const coreFaction = entity.core_faction || entity.factions?.core;
  return typeof coreFaction === "string" ? coreFaction : "";
};

export const hasDifferentCoreFaction = (player, char) => {
  if (!char || char.char_type !== "player") return false;

  const playerCoreFaction = getCoreFaction(player);
  const charCoreFaction = getCoreFaction(char);
  return Boolean(
    playerCoreFaction &&
    charCoreFaction &&
    playerCoreFaction !== charCoreFaction
  );
};

const getHostileFactions = (world, factionCode: string): string[] => {
  if (!world || !world.factions || !factionCode) return [];
  const faction = world.factions[factionCode];
  if (!faction || !Array.isArray(faction.hostile)) return [];
  return faction.hostile;
};

export const hasOpposingCoreFaction = (player, char, world) => {
  const playerCoreFaction = getCoreFaction(player);
  const charCoreFaction = getCoreFaction(char);

  if (!playerCoreFaction || !charCoreFaction || playerCoreFaction === charCoreFaction) {
    return false;
  }

  const playerHostile = getHostileFactions(world, playerCoreFaction);
  if (playerHostile.includes(charCoreFaction)) {
    return true;
  }

  const charHostile = getHostileFactions(world, charCoreFaction);
  return charHostile.includes(playerCoreFaction);
};

export const shouldShowTalkAction = (player, char, world) => {
  if (!char || char.char_type !== "mob") return false;
  return !hasOpposingCoreFaction(player, char, world);
};

export const buildCharActions = (char, player, world) => {
  const actions = {
    talk: false,
    follow: false,
    unfollow: false,
    group: false,
    list: false,
    offer: false,
    learn: false,
    unlearn: false,
    kill: false,
  } as Record<string, any>;

  const sourceActions = char && char.actions;
  if (Array.isArray(sourceActions)) {
    for (const action of sourceActions) {
      if (!action) continue;
      if (typeof action === "string") {
        actions[action] = true;
        continue;
      }
      if (typeof action === "object") {
        const actionCode = String(action.action || action.code || "").trim();
        if (actionCode) actions[actionCode] = action;
      }
    }
  } else if (sourceActions && typeof sourceActions === "object") {
    for (const [action, value] of Object.entries(sourceActions)) {
      actions[action] = value as any;
    }
  }

  if (char && char.is_merchant) {
    actions.list = true;
    actions.offer = true;
  }
  if (char && char.is_trainer) {
    if (!actions.learn) actions.learn = true;
    if (!actions.unlearn) actions.unlearn = true;
  }
  if (shouldShowTalkAction(player, char, world)) actions.talk = true;

  return actions;
};
