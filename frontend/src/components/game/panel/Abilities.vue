<template>
  <div class="abilities-region flex flex-col">
    <div class="abilities-view flex flex-col">
      <div class="abilities action-boxes" v-if="actionRows.length">
        <div class="ability-boxes">
          <div class="box-row" v-for="(row, rowIndex) in actionRows" :key="rowIndex">
            <div
              class="box-item no-touch"
              :class="{ disabled: item.isDisabled, cooldown: item.cooldownRemaining > 0 }"
              v-for="item in row"
              :key="item.key"
              @click="onClick(item)"
            >
              <div
                class="box-overlay"
                :style="item.overlayStyle"
              ></div>
              <span class="box-name unselectable">
                <span>{{ item.label }}</span>
              </span>
              <span v-if="item.cooldownLabel" class="cooldown-rounds unselectable">
                {{ item.cooldownLabel }}
              </span>
              <span v-if="item.hotKey" class="hotkey unselectable">{{ item.hotKey }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { capfirst } from "@/core/utils";

const store = useStore();

interface ActionBarItem {
  key: string;
  label: string;
  cmd: string;
  hotKey: string | null;
  isDisabled: boolean;
  cooldownRemaining: number;
  cooldownLabel: string;
  overlayStyle: Record<string, string>;
}

const HOTKEY_LIMIT = 8;
const ROW_SIZE = 4;

const player = computed(() => store.state.game.player || {});
const world = computed(() => store.state.game.world || {});

const chunkRows = (items: ActionBarItem[]) => {
  const rows: ActionBarItem[][] = [];
  for (let index = 0; index < items.length; index += ROW_SIZE) {
    rows.push(items.slice(index, index + ROW_SIZE));
  }
  return rows;
};

const onClick = (item: ActionBarItem) => {
  if (item.isDisabled) return;
  store.dispatch("game/cmd", item.cmd);
};

const abilityDefinitions = computed(() => {
  return world.value.abilities?.definitions || {};
});

const knownAbilitySlugs = computed<string[]>(() => {
  return Array.isArray(player.value.known_abilities) ? player.value.known_abilities : [];
});

const abilityHotkeys = computed<Record<string, string>>(() => {
  return player.value.ability_hotkeys || {};
});

const abilityCooldowns = computed<Record<string, number>>(() => {
  return player.value.ability_cooldowns || {};
});

const commandForAbility = (ability: any, hotKey: string | null) => {
  if (hotKey) return hotKey;
  const verbs = Array.isArray(ability.command_verbs) ? ability.command_verbs : [];
  return verbs[0] || ability.slug;
};

const abilityItem = (slug: string, hotKey: string | null): ActionBarItem | null => {
  const ability = abilityDefinitions.value[slug];
  if (!ability) return null;

  const remaining = Math.max(0, Number(abilityCooldowns.value[slug] || 0));
  const cooldownRounds = Math.max(remaining, Number(ability.cooldown?.rounds || 0));
  const cooldownPercent = cooldownRounds > 0
    ? Math.min(100, Math.max(0, Math.round((remaining / cooldownRounds) * 100)))
    : 0;

  return {
    key: `ability:${slug}`,
    label: ability.name || capfirst(slug),
    cmd: commandForAbility(ability, hotKey),
    hotKey,
    isDisabled: remaining > 0,
    cooldownRemaining: remaining,
    cooldownLabel: remaining > 0 ? `${remaining} ${remaining === 1 ? "rd" : "rds"}` : "",
    overlayStyle: { height: remaining > 0 ? `${cooldownPercent}%` : "0" },
  };
};

const abilityItems = computed(() => {
  const items: ActionBarItem[] = [];
  const usedSlugs = new Set<string>();
  const knownSet = new Set(knownAbilitySlugs.value);

  for (let hotkeyNumber = 1; hotkeyNumber <= HOTKEY_LIMIT; hotkeyNumber += 1) {
    const hotKey = String(hotkeyNumber);
    const slug = abilityHotkeys.value[hotKey];
    if (!slug || !knownSet.has(slug)) continue;

    const item = abilityItem(slug, hotKey);
    if (!item) continue;

    items.push(item);
    usedSlugs.add(slug);
  }

  for (const slug of knownAbilitySlugs.value) {
    if (usedSlugs.has(slug)) continue;

    const item = abilityItem(slug, null);
    if (item) items.push(item);
  }

  return items;
});

const actionRows = computed(() => chunkRows(abilityItems.value));
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.abilities {
  padding: 0 20px;
}

.action-boxes {
  margin-top: 10px;

  .label {
    @include font-title-light;
    color: $color-secondary;
    font-size: 15px;
    line-height: 18px;
    padding-bottom: 2px;

    > span {
      font-size: inherit;
      color: $color-text-hex-70;
    }

    > .stance {
      text-transform: lowercase;
      margin-left: 35px;
      float: right;
      font-size: 17px;
    }
  }

  .box-row {
    display: flex;
    position: relative;

    .box-item {
      position: relative;

      flex: 0 0 25%;
      display: flex;
      justify-content: center;
      align-items: center;
      text-align: center;
      flex-wrap: wrap;

      color: white;
      background: #828283;

      &.disabled {
        background: #3c3c3c;
      }

      height: 40px;
      max-width: 25%;
      min-width: 0;

      font-size: 11px;
      line-height: 15px;

      &:not(:last-child) {
        border-right: 1px solid $color-background-black;
      }

      .hotkey {
        display: block;
        position: absolute;
        bottom: 1px;
        right: 4px;
        z-index: 2;
        @include font-title-regular;
        color: $color-background-black;
        font-size: 10px;
      }

      &.no-touch {
        &:hover {
          cursor: pointer;

          &.disabled {
            cursor: default;
          }
        }
      }

      &.cooldown {
        background: #3c3c3c;
        color: rgba(255, 255, 255, 0.3);

        .hotkey {
          color: white;
        }
      }

      .box-name {
        @include font-text-regular;
        position: absolute;
        left: 0;
        top: 0;
        z-index: 2;
        box-sizing: border-box;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        height: 100%;
        padding: 0 10px 0 6px;
        overflow: hidden;
        text-align: center;
        white-space: normal;
        line-height: 12px;

        color: white;

        > span {
          display: block;
          width: 100%;
          min-width: 0;
          overflow-wrap: break-word;
          word-break: normal;
        }
      }

      .cooldown-rounds {
        @include font-title-regular;
        position: absolute;
        left: 4px;
        bottom: 1px;
        z-index: 3;
        color: white;
        font-size: 10px;
        line-height: 12px;
      }

      &.disabled > .box-name {
        color: $color-text-hex-50;
      }

      .box-overlay {
        position: absolute;
        bottom: 0;
        left: 0;
        z-index: 1;
        background: #3c3c3c;
        width: 100%;
        height: 0;
      }
    }

    &:first-child {
      .box-item:first-child {
        border-top-left-radius: 2px;

        .box-overlay {
          border-top-left-radius: 2px;
        }
      }

      .box-item:last-child {
        border-top-right-radius: 2px;

        .box-overlay {
          border-top-right-radius: 2px;
        }
      }
    }

    &:last-child {
      .box-item:first-child {
        border-bottom-left-radius: 2px;

        .box-overlay {
          border-bottom-left-radius: 2px;
        }
      }

      .box-item:last-child {
        border-bottom-right-radius: 2px;

        .box-overlay {
          border-bottom-right-radius: 2px;
        }
      }
    }

    &:not(:only-child) {
      &:not(:last-child) {
        border-bottom: 1px solid $color-background-black;
      }
    }
  }
}
</style>
