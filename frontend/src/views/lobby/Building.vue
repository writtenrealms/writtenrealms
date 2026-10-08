<template>
  <div id="page" class="building-page">
    <header class="building-header">
      <div>
        <h1 class="section-title">Your Worlds</h1>
        <p v-if="loaded && worlds.length" class="building-summary color-text-60">{{ summary }}</p>
      </div>
      <router-link v-if="canCreateWorlds" :to="{ name: 'world-create' }" class="btn-medium create-world" role="button">
        <Plus aria-hidden="true" />CREATE WORLD
      </router-link>
    </header>

    <p v-if="!canCreateWorlds && !isStaff" class="building-closed color-text-60">
      World creation is closed right now. You can keep building worlds you author or were added to.
    </p>

    <div v-if="loadError" class="building-message" role="alert">
      <p>Your worlds couldn't be loaded.</p>
      <button class="btn-small" @click="load">RETRY</button>
    </div>

    <div v-else-if="!loaded" class="loading-container building-loading"><div class="spinner"></div></div>

    <div v-else-if="!worlds.length" class="building-message panel">
      <template v-if="canCreateWorlds">
        <p>You haven't built a world yet. Worlds you create, and worlds other builders add you to, show up here.</p>
        <router-link :to="{ name: 'world-create' }" class="btn-medium" role="button">CREATE YOUR FIRST WORLD</router-link>
      </template>
      <p v-else>You don't have any worlds to build yet. When another builder adds you to their world, it will show up here.</p>
    </div>

    <template v-else>
      <div class="building-toolbar">
        <label class="building-search">
          <Search aria-hidden="true" />
          <input v-model="query" type="search" placeholder="Search worlds and instances" aria-label="Search worlds and instances" />
        </label>
        <div v-if="hasSharedWorlds" class="building-filter" role="group" aria-label="Show">
          <button v-for="option in roleOptions" :key="option.value" :aria-pressed="role === option.value"
            @click="role = option.value">{{ option.label }}</button>
        </div>
        <label class="building-sort">
          <span class="color-text-50">Sort</span>
          <select v-model="sort" aria-label="Sort worlds">
            <option v-for="option in sortOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
      </div>

      <div class="world-list-head" aria-hidden="true">
        <span></span><span>World</span><span>Rooms</span><span>Characters</span><span>Opened</span><span></span>
      </div>

      <article v-for="world in visibleWorlds" :key="world.id" class="world-item">
        <div class="world-row">
          <router-link class="world-thumb" :to="builderRoute(world)" :style="thumbStyle(world)" tabindex="-1" aria-hidden="true">
            <span v-if="!world.small_background">{{ world.name.charAt(0) }}</span>
          </router-link>
          <div class="world-main">
            <div class="world-name-line">
              <router-link class="world-name" :to="builderRoute(world)">{{ world.name }}</router-link>
              <span v-if="world.role === 'builder'" class="tag">Builder</span>
              <span v-if="reviewTags[world.review_status]" class="tag" :class="reviewTags[world.review_status].className">
                {{ reviewTags[world.review_status].label }}
              </span>
            </div>
            <div class="world-meta color-text-50">
              {{ world.is_multiplayer ? 'Multiplayer' : 'Single player' }} · {{ world.is_public ? 'Public' : 'Private' }}
              <template v-if="world.instance_of"> · Instance of {{ world.instance_of.name }}</template>
            </div>
          </div>
          <span class="world-stat" data-label="Rooms">{{ world.num_rooms.toLocaleString() }}</span>
          <span class="world-stat" data-label="Characters">{{ world.num_characters.toLocaleString() }}</span>
          <span class="world-stat world-opened" data-label="Opened">{{ opened(world) }}</span>
          <div class="world-actions">
            <router-link :to="builderRoute(world)" class="btn-small" role="button">BUILD</router-link>
            <router-link :to="lobbyRoute(world)" class="icon-button" :aria-label="`Open the lobby page for ${world.name}`"
              title="Open lobby page">
              <DoorOpen aria-hidden="true" />
            </router-link>
          </div>
        </div>

        <div v-for="instance in sortedWorlds(world.instances)" :key="instance.id" class="world-row instance-row">
          <CornerDownRight class="instance-marker" aria-hidden="true" />
          <div class="world-main">
            <div class="world-name-line">
              <router-link class="world-name" :to="builderRoute(instance)">{{ instance.name }}</router-link>
              <span class="tag">Instance</span>
              <span v-if="instance.role === 'builder'" class="tag">Builder</span>
            </div>
          </div>
          <span class="world-stat" data-label="Rooms">{{ instance.num_rooms.toLocaleString() }}</span>
          <span class="world-stat" data-label="Characters">{{ instance.num_characters.toLocaleString() }}</span>
          <span class="world-stat world-opened" data-label="Opened">{{ opened(instance) }}</span>
          <div class="world-actions">
            <router-link :to="builderRoute(instance)" class="btn-small" role="button">BUILD</router-link>
          </div>
        </div>
      </article>

      <div v-if="!visibleWorlds.length" class="building-message">
        <p class="color-text-60">No worlds match your search.</p>
        <button class="btn-thin" @click="query = ''; role = 'all'">CLEAR FILTERS</button>
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref } from "vue";
import axios from "axios";
import { CornerDownRight, DoorOpen, Plus, Search } from "@lucide/vue";
import { useBuilding } from "@/composables/useBuilding";
import { formatRelativeModifiedDate } from "@/core/utils";

interface BuildingWorld {
  id: number;
  name: string;
  small_background: string;
  is_multiplayer: boolean;
  is_public: boolean;
  role: "author" | "builder";
  num_rooms: number;
  num_characters: number;
  last_opened: string | null;
  created_ts: string;
  review_status: string;
  instance_of: { id: number; name: string } | null;
  instances: BuildingWorld[];
}

const { isStaff, canCreateWorlds } = useBuilding();

const worlds = ref<BuildingWorld[]>([]);
const loaded = ref(false);
const loadError = ref(false);
const query = ref("");
const role = ref<"all" | "author" | "builder">("all");
const sort = ref("opened");

const roleOptions = [
  { value: "all", label: "All" },
  { value: "author", label: "Authored" },
  { value: "builder", label: "Shared with me" },
] as const;

const sortOptions = [
  { value: "opened", label: "Recently opened" },
  { value: "name", label: "Name" },
  { value: "created", label: "Newest" },
  { value: "rooms", label: "Most rooms" },
  { value: "characters", label: "Most characters" },
];

const reviewTags: Record<string, { label: string; className: string }> = {
  submitted: { label: "In review", className: "tag-secondary" },
  approved: { label: "Approved", className: "tag-green" },
  reviewed: { label: "Changes requested", className: "tag-primary" },
};

async function load() {
  loadError.value = false;
  try {
    const response = await axios.get("lobby/worlds/building/");
    worlds.value = response.data;
    loaded.value = true;
  } catch (error) {
    loadError.value = true;
  }
}
onMounted(load);

const hasSharedWorlds = computed(() => worlds.value.some(world => world.role === "builder"));

const summary = computed(() => {
  const instanceCount = worlds.value.reduce((count, world) => count + world.instances.length, 0);
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
  return instanceCount
    ? `${plural(worlds.value.length, "world")} and ${plural(instanceCount, "instance")}`
    : plural(worlds.value.length, "world");
});

// The server returns worlds most recently opened first.
const sortedWorlds = (list: BuildingWorld[]) => {
  if (sort.value === "opened") return list;
  const sorted = [...list];
  if (sort.value === "name") sorted.sort((a, b) => a.name.localeCompare(b.name));
  if (sort.value === "created") sorted.sort((a, b) => b.created_ts.localeCompare(a.created_ts));
  if (sort.value === "rooms") sorted.sort((a, b) => b.num_rooms - a.num_rooms);
  if (sort.value === "characters") sorted.sort((a, b) => b.num_characters - a.num_characters);
  return sorted;
};

const visibleWorlds = computed(() => {
  const search = query.value.trim().toLowerCase();
  const matches = (world: BuildingWorld) => world.name.toLowerCase().includes(search);
  return sortedWorlds(worlds.value.filter(world =>
    (role.value === "all" || world.role === role.value)
    && (!search || matches(world) || world.instances.some(matches))));
});

const opened = (world: BuildingWorld) => world.last_opened ? formatRelativeModifiedDate(world.last_opened) : "Never";
const builderRoute = (world: BuildingWorld) => ({ name: "builder_world_index", params: { world_id: world.id } });
const lobbyRoute = (world: BuildingWorld) => ({ name: "lobby_world_details", params: { world_id: world.id } });
const thumbStyle = (world: BuildingWorld) =>
  world.small_background ? { backgroundImage: `url(${world.small_background})` } : {};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
@import "@/styles/layout.scss";

// Thumbnail, name, three stats and actions. Instance rows reuse the columns
// so their numbers line up with the world above them.
$columns: 112px minmax(0, 1fr) 84px 96px 84px 112px;

.building-page {
  padding-top: 30px;
  padding-bottom: 80px;
}

.building-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 24px;

  .section-title {
    margin: 0;
  }

  .building-summary {
    margin: 6px 0 0;
  }

  .create-world {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    text-decoration: none;
  }
}

.building-closed {
  margin: -8px 0 24px;
}

.building-loading {
  height: 40vh;
}

.building-message {
  padding: 24px 0;

  &.panel {
    padding: 24px;
  }

  p {
    margin-bottom: 16px;
    max-width: 60ch;
  }
}

.building-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 20px;
  margin-bottom: 18px;
}

.building-search {
  flex: 1 1 260px;
  max-width: 360px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  background: $color-form-background;
  border: 1px solid $color-form-border;
  border-radius: 2px;
  color: $color-text-hex-50;

  &:focus-within {
    border-color: $color-text-hex-60;
  }

  input {
    flex: 1;
    min-width: 0;
    padding: 8px 0;
    background: none;
    border: 0;
    outline: none;
    color: $color-text;
    font-size: 13px;
  }
}

.building-filter {
  display: flex;
  border: 1px solid $color-background-border;
  border-radius: 2px;

  button {
    @include font-title-regular;
    font-size: 12px;
    text-transform: uppercase;
    padding: 6px 12px;
    background: none;
    border: 0;
    color: $color-text-hex-60;

    & + button {
      border-left: 1px solid $color-background-border;
    }

    &:hover {
      color: $color-text;
    }

    &[aria-pressed="true"] {
      background: $color-background-very-light;
      color: $color-text;
    }
  }
}

.building-sort {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  font-size: 13px;

  select {
    background: $color-form-background;
    border: 1px solid $color-form-border;
    border-radius: 2px;
    color: $color-text;
    padding: 6px 8px;
    font-size: 13px;
  }
}

.world-list-head {
  @include font-title-regular;
  display: grid;
  grid-template-columns: $columns;
  gap: 16px;
  padding: 0 0 8px;
  font-size: 11px;
  text-transform: uppercase;
  color: $color-text-hex-50;
  border-bottom: 1px solid $color-background-light-border;

  span:nth-child(n + 3):nth-child(-n + 5) {
    text-align: right;
  }
}

.world-item {
  border-bottom: 1px solid $color-background-light-border;
  padding: 14px 0;
}

.world-row {
  display: grid;
  grid-template-columns: $columns;
  gap: 16px;
  align-items: center;
}

.world-thumb {
  display: grid;
  place-items: center;
  width: 112px;
  aspect-ratio: 740 / 332;
  border-radius: 2px;
  background:
    linear-gradient(135deg, rgba(245, 201, 131, 0.16), rgba(215, 118, 23, 0.05)),
    $color-background-light;
  background-size: cover;
  background-position: center;
  border: 1px solid $color-background-light-border;
  text-decoration: none;

  span {
    @include font-title-regular;
    font-size: 22px;
    color: $color-secondary;
  }
}

.world-main {
  min-width: 0;
}

.world-name-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
}

.world-name {
  @include font-text-regular;
  font-size: 16px;
  color: $color-text;
  overflow-wrap: anywhere;

  &:hover {
    color: $color-primary;
  }
}

.world-meta {
  font-size: 13px;
  margin-top: 2px;
}

.world-stat {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: $color-text-hex-70;
  font-size: 14px;
}

.world-opened {
  font-size: 13px;
  color: $color-text-hex-50;
  white-space: nowrap;
}

.world-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 4px;

  .btn-small {
    text-decoration: none;
  }
}

.instance-row {
  margin-top: 10px;

  .instance-marker {
    justify-self: end;
    color: $color-text-hex-40;
  }

  .world-name {
    font-size: 14px;
  }
}

@media ($mobile-site) {
  .world-list-head {
    display: none;
  }

  .building-sort {
    margin-left: 0;
  }

  .world-row {
    grid-template-columns: 72px minmax(0, 1fr) auto;
    row-gap: 8px;
  }

  .world-thumb {
    width: 72px;
  }

  // Stats sit on their own line under the name, labelled inline.
  .world-stat {
    grid-row: 2;
    text-align: left;
    font-size: 12px;

    &::before {
      content: attr(data-label) " ";
      color: $color-text-hex-50;
    }
  }

  .world-stat:nth-of-type(1) { grid-column: 2; }
  .world-stat:nth-of-type(2) { grid-column: 3; }
  .world-stat:nth-of-type(3) { display: none; }

  .world-actions {
    grid-column: 3;
    grid-row: 1;
  }
}
</style>
