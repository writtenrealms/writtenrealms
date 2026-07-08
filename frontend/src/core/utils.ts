export const capfirst = word => {
  if (!word) return "";
  return word.charAt(0).toUpperCase() + word.slice(1);
};

const thisYearDateFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric"
});
const priorYearDateFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  year: "numeric"
});

export const formatRelativeModifiedDate = (value?: string) => {
  if (!value) {
    return "";
  }

  const modifiedAt = new Date(value);
  if (Number.isNaN(modifiedAt.getTime())) {
    return "";
  }

  const now = new Date();
  const diffMs = now.getTime() - modifiedAt.getTime();
  if (diffMs < 60000) {
    return "now";
  }

  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }

  if (modifiedAt.getFullYear() === now.getFullYear()) {
    return thisYearDateFormatter.format(modifiedAt);
  }

  return priorYearDateFormatter.format(modifiedAt);
};

export const formatPercent = value => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value || 0);
  const rounded = Math.round(numeric * 100) / 100;
  return String(rounded).replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
};

export const resourcePercent = (current, maximum) => {
  const currentValue = Number(current ?? 0);
  const maximumValue = Number(maximum ?? 0);
  if (!Number.isFinite(currentValue) || !Number.isFinite(maximumValue) || maximumValue <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(100, (currentValue / maximumValue) * 100));
};

export const getRatingConfigForStat = (world, key) => {
  const ratings = world?.combat?.ratings || {};
  const direct = ratings[key];
  if (direct && (!direct.stat || direct.stat === key)) return direct;
  return Object.values(ratings).find((rating: any) => rating?.stat === key);
};

export const formatCombatStatValue = (world, player, key, value, mode = "dash") => {
  const percentMap: any = {
    armor: player?.armor_perc,
    crit: player?.crit_perc,
    dodge: player?.dodge_perc,
    resilience: player?.resilience_perc,
  };
  const perc = percentMap[key];
  const ratingConfig: any = getRatingConfigForStat(world, key);

  if (ratingConfig?.type === "percentage_points") {
    const percentValue = perc !== undefined && perc !== null ? perc : value;
    return `${formatPercent(percentValue)}%`;
  }

  if (perc !== undefined && perc !== null) {
    const formatted = formatPercent(perc);
    return mode === "paren" ? `${value} (${formatted}%)` : `${value} - ${formatted}%`;
  }

  return `${value}`;
};

export const stackedInventory = function(inv) {
  /*
    Takes a list of items and consolidates those with identical backend-provided
    stack keys to the count of the already encountered item.
  */

  var c_inv: any[] = []; // the inventory with stack counts
  var t_items = {}; // stackable items cache

  for (let item of inv) {
    var definitionId = item.definition_id;
    var stackKey = item.stack_key;
    if (stackKey === undefined && definitionId && !item.is_container) {
      stackKey = `definition:${definitionId}`;
    }

    // Stackable item
    if (stackKey) {
      if (t_items[stackKey] === undefined) {
        item.display_key = stackKey;
        t_items[stackKey] = item;
        item.count = 1;
        item.showCount = false;
        c_inv.push(item);
      } else {
        // Modify item in cache
        if (t_items[stackKey].count === 1) t_items[stackKey].showCount = true;
        t_items[stackKey].count += 1;
      }

    // Generated or otherwise unique item
    } else {
      item.display_key = item.key;
      c_inv.push(item);
    }
  }

  return c_inv;
};

export const getTargetInGroup = (entity, group, actor?) => {
  // Often when trying to generate a text command for an item or a mob,
  // there could be multiple copies of the item or the mob in the context
  // that is being considered.
  //
  // This function returns a target string which qualifies the entity
  // in the context of the rom it is in.
  //
  // Returns 0 if one could not be found.

  // Since we know at least the entity itself is a duplicate of itself,
  // we start at 1.
  let duplicateCount = 1,
    found = false;
  const entityKeyword = entity && entity.keyword
    ? entity.keyword
    : ((entity && entity.keywords) ? entity.keywords.split(" ")[0] : "");

  for (const thing of group) {
    if (actor && thing.key === actor.key) {
      continue;
    }

    if (entity.key === thing.key) {
      found = true;
      if (entity.key.split(".")[0] === "player") {
        return entity.name;
      }
      break;
    }
    const foundIndex = thing.keywords.split(" ").indexOf(entityKeyword);
    if (foundIndex !== -1) {
      duplicateCount += 1;
    }
  }
  if (!found) {
    return 0;
  }

  let target = entityKeyword;
  if (duplicateCount > 1) {
    target = `${duplicateCount}.${target}`;
  }
  return target;
};


export const parseLinks = (line) => {
  return line.replace(
      /((http|https):\/\/[\w?=&.\/-;#~%-]+(?![\w\s?&.\/;#~%"=-]*>))/g,
      "<a href='$1' class='interactive' target='_blank'>$1</a>"
  );
};
