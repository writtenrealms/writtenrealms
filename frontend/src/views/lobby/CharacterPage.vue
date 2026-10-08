<template>
  <div id="character_page">
    <div v-if="loadError" class="char-message" role="alert">
      <p>{{ loadError }}</p>
      <router-link to="/lobby">Back to the lobby</router-link>
    </div>

    <div v-else-if="!data" class="loading-container"><div class="spinner"></div></div>

    <template v-else>
      <header class="char-hero">
        <div class="char-hero-art" :style="heroStyle"></div>
        <div class="char-hero-content">
          <router-link class="char-back" :to="{ name: 'lobby_world_details', params: { world_id: data.world.id } }">
            <ArrowLeft :size="16" aria-hidden="true" />{{ data.world.name }}
          </router-link>
          <h1 class="char-name">{{ data.name }}</h1>
          <div v-if="data.title" class="char-title">{{ data.title }}</div>
          <div class="char-meta">
            <span>{{ headline }}</span>
            <span v-if="data.is_builder" class="tag">Builder</span>
            <span v-if="data.in_game" class="tag tag-green">In game</span>
          </div>
          <div v-if="isPrivate" class="char-actions">
            <button class="btn-medium" @click="playChar(data, data.world.id)">
              {{ needsTransfer(data) ? 'TRANSFER' : 'PLAY AS' }} {{ data.name.toUpperCase() }}
            </button>
          </div>
        </div>
      </header>

      <!-- Owner's view -->
      <div v-if="isPrivate" class="char-body">
        <div class="char-main">
          <p v-if="!data.can_manage_items" class="char-notice">
            {{ data.name }} is in the game right now. Manage items there, or come back once they leave.
          </p>

          <section class="char-section">
            <h2>Equipment</h2>
            <div class="item-rows">
              <div v-for="slot in equipmentSlots" :key="slot.key" class="item-row" :class="{ selected: isSelected(slot.item) }">
                <span class="slot-label">{{ slot.label }}</span>
                <button v-if="slot.item" class="item-name" :class="slot.item.quality" v-bind="itemPreviewBindings(slot.item)">
                  {{ slot.item.name }}
                </button>
                <span v-else class="item-empty">Empty</span>
                <div class="item-actions">
                  <button v-if="slot.item" class="btn-thin" :disabled="!canAct" @click="act('remove', slot.item)">REMOVE</button>
                </div>
              </div>
            </div>
          </section>

          <section class="char-section">
            <h2>Inventory <span class="section-count">{{ inventory.length }}</span></h2>
            <p v-if="!inventory.length" class="color-text-50">{{ data.name }} isn't carrying anything.</p>
            <div v-else class="item-rows">
              <template v-for="item in inventory" :key="item.key">
                <div class="item-row inventory-row" :class="{ selected: isSelected(item) }">
                  <button v-if="isBag(item)" class="icon-button bag-toggle" :aria-expanded="!!openBags[item.key]"
                    :aria-label="`${openBags[item.key] ? 'Hide' : 'Show'} what's in ${item.name}`" @click="toggleBag(item)">
                    <ChevronDown v-if="openBags[item.key]" aria-hidden="true" />
                    <ChevronRight v-else aria-hidden="true" />
                  </button>
                  <span v-else class="bag-toggle"></span>
                  <button class="item-name" :class="item.quality" v-bind="itemPreviewBindings(item)">{{ item.name }}</button>
                  <div class="item-actions">
                    <button v-if="item.type === 'equippable'" class="btn-small" :disabled="!canAct" @click="act('equip', item)">EQUIP</button>
                    <select v-if="!isBag(item) && bags.length" class="put-select" :disabled="!canAct"
                      :aria-label="`Put ${item.name} in a bag`" value="" @change="onPut(item, $event)">
                      <option value="" disabled>Put in…</option>
                      <option v-for="bag in bags" :key="bag.key" :value="bag.key">{{ bag.name }}</option>
                    </select>
                  </div>
                </div>
                <template v-if="isBag(item) && openBags[item.key]">
                  <div v-for="content in item.inventory || []" :key="content.key" class="item-row bag-content-row"
                    :class="{ selected: isSelected(content) }">
                    <span class="bag-toggle"></span>
                    <button class="item-name" :class="content.quality" v-bind="itemPreviewBindings(content)">{{ content.name }}</button>
                    <div class="item-actions">
                      <button class="btn-thin" :disabled="!canAct" @click="act('take', content, item)">TAKE OUT</button>
                    </div>
                  </div>
                  <div v-if="!(item.inventory || []).length" class="item-row bag-content-row">
                    <span class="bag-toggle"></span><span class="item-empty">Empty</span>
                  </div>
                </template>
              </template>
            </div>
          </section>

          <section class="char-section">
            <div class="section-head">
              <h2>Description</h2>
              <button v-if="!editingDescription" class="btn-thin" @click="startEditing">EDIT</button>
            </div>
            <form v-if="editingDescription" class="form-group" @submit.prevent="saveDescription">
              <label for="char-description" class="sr-only">Description</label>
              <textarea id="char-description" v-model="draftDescription" rows="5" maxlength="5000"></textarea>
              <div class="description-actions">
                <button class="btn-small" :disabled="saving">{{ saving ? 'SAVING…' : 'SAVE' }}</button>
                <button type="button" class="btn-small button-gray" @click="editingDescription = false">CANCEL</button>
              </div>
            </form>
            <p v-else-if="data.description" class="char-description">{{ data.description }}</p>
            <p v-else class="color-text-50">No description yet. Other players see it when they look at {{ data.name }}.</p>
          </section>
        </div>

        <aside class="char-side">
          <section class="char-section">
            <h2>Level {{ character.level }}</h2>
            <div class="xp-bar" role="img" :aria-label="`${xpPercent}% of the way to the next level`">
              <div class="xp-fill" :style="{ width: `${xpPercent}%` }"></div>
            </div>
            <p class="xp-label color-text-50">{{ xpLabel }}</p>
          </section>

          <section v-if="attributeEntries.length" class="char-section">
            <h2>Attributes</h2>
            <table class="data-table key-value-table">
              <tbody>
                <tr v-for="entry in attributeEntries" :key="entry.key"><th scope="row">{{ entry.label }}</th><td>{{ entry.value }}</td></tr>
              </tbody>
            </table>
          </section>

          <section v-if="statEntries.length" class="char-section">
            <h2>Combat</h2>
            <table class="data-table key-value-table">
              <tbody>
                <tr v-for="entry in statEntries" :key="entry.key"><th scope="row">{{ entry.label }}</th><td>{{ entry.value }}</td></tr>
              </tbody>
            </table>
          </section>

          <section v-if="walletEntries.length || materialEntries.length" class="char-section">
            <h2>Purse</h2>
            <table class="data-table key-value-table">
              <tbody>
                <tr v-for="entry in walletEntries" :key="entry.currency"><th scope="row">{{ entry.label }}</th><td>{{ entry.amount }}</td></tr>
                <tr v-for="entry in materialEntries" :key="entry.key"><th scope="row">{{ entry.label }}</th><td>{{ entry.amount }}</td></tr>
                <tr><th scope="row">Glory</th><td>{{ data.glory }}</td></tr>
              </tbody>
            </table>
          </section>

          <section class="char-section char-danger">
            <button class="btn-small button-red" :disabled="data.in_game" @click="confirmDelete">DELETE CHARACTER</button>
            <p v-if="data.in_game" class="color-text-50">Characters can't be deleted while they're in the game.</p>
          </section>
        </aside>
      </div>

      <!-- Everyone else's view -->
      <div v-else class="char-body">
        <div class="char-main">
          <section class="char-section">
            <h2>Description</h2>
            <p v-if="data.description" class="char-description">{{ data.description }}</p>
            <p v-else class="color-text-50">{{ data.name }} hasn't written a description.</p>
          </section>
          <section class="char-section">
            <h2>Wearing</h2>
            <p v-if="!data.equipment.length" class="color-text-50">Nothing worth mentioning.</p>
            <div v-else class="item-rows">
              <div v-for="worn in data.equipment" :key="worn.slot" class="item-row">
                <span class="slot-label">{{ slotLabel(worn.slot) }}</span>
                <span class="item-name static" :class="worn.quality">{{ worn.name }}</span>
              </div>
            </div>
          </section>
        </div>
        <aside class="char-side">
          <table class="data-table key-value-table">
            <tbody>
              <tr v-if="data.core_faction"><th scope="row">Faction</th><td>{{ data.core_faction }}</td></tr>
              <tr><th scope="row">Class</th><td>{{ capfirst(data.archetype) }}</td></tr>
              <tr><th scope="row">Level</th><td>{{ data.level }}</td></tr>
              <tr><th scope="row">Glory</th><td>{{ data.glory }}</td></tr>
              <tr><th scope="row">Created</th><td>{{ createdLabel }}</td></tr>
            </tbody>
          </table>
        </aside>
      </div>

      <ItemPreview v-if="preview" :preview="preview" :player="character" :world="worldConfig"
        @close="closePreview" @keep-open="keepPreviewOpen" @leave="leavePreview" />
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import axios from "axios";
import { ArrowLeft, ChevronDown, ChevronRight } from "@lucide/vue";
import ItemPreview from "@/components/ui/ItemPreview.vue";
import { useItemPreview } from "@/composables/useItemPreview";
import DeleteCharacterDialog from "@/components/lobby/DeleteCharacterDialog.vue";
import { useCharEntry } from "@/composables/useCharEntry";
import { walletBalanceEntries } from "@/core/economy";
import { capfirst, formatCombatStatValue } from "@/core/utils";

const SLOTS = [
  ["weapon", "Weapon"], ["offhand", "Off hand"], ["head", "Head"], ["shoulders", "Shoulders"],
  ["body", "Body"], ["arms", "Arms"], ["hands", "Hands"], ["waist", "Waist"],
  ["legs", "Legs"], ["feet", "Feet"], ["accessory", "Accessory"],
];

const route = useRoute();
const router = useRouter();
const store = useStore();
const { needsTransfer, playChar } = useCharEntry();

const data = ref<any>(null);
const loadError = ref("");
const busy = ref(false);
const { preview, bindings: itemPreviewBindings, close: closePreview,
  keepOpen: keepPreviewOpen, leave: leavePreview } = useItemPreview();
watch(data, closePreview);
const openBags = reactive<Record<string, boolean>>({});
const editingDescription = ref(false);
const draftDescription = ref("");
const saving = ref(false);

async function load() {
  data.value = null;
  loadError.value = "";
  try {
    const response = await axios.get(`/lobby/characters/${route.params.player_id}/`);
    data.value = response.data;
  } catch (error: any) {
    loadError.value = error.response?.status === 404
      ? "This character doesn't exist, or you don't have access to the world it plays in."
      : "This character couldn't be loaded. Please try again.";
  }
}
watch(() => route.params.player_id, (id) => { if (id) load(); }, { immediate: true });

const isPrivate = computed(() => data.value?.view === "private");
const character = computed(() => data.value?.character || {});
const worldConfig = computed(() => data.value?.world_config || {});
const canAct = computed(() => isPrivate.value && data.value.can_manage_items && !busy.value);

const headline = computed(() => {
  const source = isPrivate.value ? character.value : data.value;
  const faction = data.value.core_faction ? `${data.value.core_faction} ` : "";
  return `${faction}${source.archetype || ""} · Level ${source.level}`.trim();
});

const heroStyle = computed(() => {
  const art = data.value.world.large_background || data.value.world.small_background;
  return art ? { backgroundImage: `url(${art})` } : {};
});

const slotLabel = (slot: string) => (SLOTS.find(([key]) => key === slot) || [slot, slot])[1];

const equipmentSlots = computed(() => {
  const equipment = character.value.equipment || {};
  return SLOTS
    .filter(([key]) => key in equipment)
    .map(([key, label]) => ({ key, label, item: equipment[key] }));
});

const inventory = computed(() => character.value.inventory || []);
const isBag = (item: any) => item.type === "container";
const bags = computed(() => inventory.value.filter(isBag));

const isSelected = (item: any) => !!item && item.key === preview.value?.item.key;
const toggleBag = (bag: any) => {
  closePreview();
  openBags[bag.key] = !openBags[bag.key];
};

async function act(action: string, item: any, container: any = null) {
  if (!canAct.value) return;
  closePreview();
  busy.value = true;
  try {
    const response = await axios.post(`/lobby/characters/${data.value.id}/items/`, {
      action, item: item.key, container: container?.key,
    });
    data.value = response.data;
    if (action === "put") openBags[container.key] = true;
  } catch (error: any) {
    const detail = error.response?.data?.detail || error.response?.data?.container || error.response?.data?.item;
    store.commit("ui/notification_set_error", detail || "That didn't work. Please try again.");
  } finally {
    busy.value = false;
  }
}

const onPut = (item: any, event: Event) => {
  const select = event.target as HTMLSelectElement;
  const bag = bags.value.find((candidate: any) => candidate.key === select.value);
  select.value = "";
  if (bag) act("put", item, bag);
};

const startEditing = () => {
  draftDescription.value = data.value.description || "";
  editingDescription.value = true;
};

async function saveDescription() {
  saving.value = true;
  try {
    const response = await axios.patch(`/lobby/characters/${data.value.id}/`, { description: draftDescription.value });
    data.value = response.data;
    editingDescription.value = false;
  } catch (error: any) {
    store.commit("ui/notification_set_error", error.response?.data?.description || "The description couldn't be saved.");
  } finally {
    saving.value = false;
  }
}

const confirmDelete = () => {
  const worldId = data.value.world.id;
  store.commit("ui/modal/open_view", {
    component: DeleteCharacterDialog,
    props: {
      player: { id: data.value.id, name: data.value.name },
      afterDelete: () => router.push({ name: "lobby_world_details", params: { world_id: worldId } }),
    },
  });
};

const xpPercent = computed(() => {
  const progress = Number(character.value.experience_progress) || 0;
  const needed = Number(character.value.experience_needed) || 0;
  if (!needed) return 100;
  return Math.max(0, Math.min(100, Math.round((progress / needed) * 100)));
});
const xpLabel = computed(() => {
  const needed = Number(character.value.experience_needed) || 0;
  if (!needed) return `Max level · ${(character.value.experience || 0).toLocaleString()} experience`;
  return `${(character.value.experience_progress || 0).toLocaleString()} / ${needed.toLocaleString()} experience to level ${character.value.level + 1}`;
});

const labelEntries = (values: Record<string, any>, labels: Record<string, string>, order: string[] | undefined, format?: (key: string, value: any) => any) =>
  (order || Object.keys(values))
    .filter(key => values[key] !== undefined)
    .map(key => ({ key, label: labels[key] || capfirst(key.replace(/_/g, " ")), value: format ? format(key, values[key]) : values[key] }));

const attributeEntries = computed(() => labelEntries(
  character.value.attributes || {}, worldConfig.value.labels?.attributes || {}, worldConfig.value.labels?.order?.attributes,
  (_key, value) => Math.round(Number(value) || 0)));
const statEntries = computed(() => labelEntries(
  character.value.stats || {}, worldConfig.value.labels?.stats || {}, worldConfig.value.labels?.order?.stats,
  (key, value) => formatCombatStatValue(worldConfig.value, character.value, key, value, "paren")));
const walletEntries = computed(() => walletBalanceEntries(worldConfig.value.economy, character.value.economy));
const materialEntries = computed(() => {
  const materials = character.value.materials || {};
  const catalog = worldConfig.value.craft_materials || [];
  const names: Record<string, string> = Array.isArray(catalog)
    ? Object.fromEntries(catalog.map((entry: any) => [entry.slug, entry.name]))
    : Object.fromEntries(Object.entries(catalog).map(([slug, entry]: any) => [slug, entry?.name || slug]));
  return Object.entries(materials).map(([key, amount]) => ({ key, label: names[key] || capfirst(key.replace(/[-_]/g, " ")), amount }));
});

const createdLabel = computed(() => new Date(data.value.created_ts).toLocaleDateString(undefined, { year: "numeric", month: "long" }));
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";
@import "@/styles/layout.scss";

#character_page {
  width: 100%;
}

.char-message {
  max-width: 600px;
  margin: 60px auto;
  padding: 0 20px;
}

// A shorter take on the world lobby's banner.
.char-hero {
  position: relative;
  isolation: isolate;
  min-height: 260px;
  display: flex;
  align-items: flex-end;
}

.char-hero-art {
  position: absolute;
  inset: 0;
  z-index: -1;
  background-color: #332d25;
  background-size: cover;
  background-position: center 30%;

  &::after {
    content: "";
    position: absolute;
    inset: 0;
    background:
      linear-gradient(90deg, rgba(25, 26, 28, 0.85) 0%, rgba(25, 26, 28, 0.55) 45%, rgba(25, 26, 28, 0.35) 100%),
      linear-gradient(180deg, rgba(25, 26, 28, 0.2) 0%, rgba(25, 26, 28, 0.75) 70%, $color-background-rgba 100%);
  }
}

.char-hero-content,
.char-body {
  width: 100%;
  max-width: $site-max-width;
  margin: 0 auto;
  padding-left: 80px;
  padding-right: 30px;

  @media ($mobile-site) {
    padding-left: 15px;
    padding-right: 15px;
  }
}

.char-hero-content {
  padding-top: 40px;
  padding-bottom: 28px;
}

.char-back {
  @include font-title-regular;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  text-transform: uppercase;
  color: $color-text-hex-70;
  margin-bottom: 14px;

  &:hover {
    color: $color-primary;
  }
}

.char-name {
  @include font-title-regular;
  font-size: 44px;
  line-height: 1;
  letter-spacing: 3px;
  text-transform: uppercase;
  text-shadow: 0 2px 18px rgba(0, 0, 0, 0.5);
  overflow-wrap: anywhere;

  @media ($mobile-site) {
    font-size: 32px;
  }
}

.char-title {
  @include font-text-light;
  font-style: italic;
  font-size: 16px;
  margin-top: 6px;
  color: $color-text-hex-80;
}

.char-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 15px;
  color: $color-text-hex-70;

  span:first-child {
    text-transform: capitalize;
  }
}

.char-actions {
  margin-top: 20px;
}

.char-body {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 56px;
  padding-top: 20px;
  padding-bottom: 80px;

  @media ($mobile-site) {
    grid-template-columns: minmax(0, 1fr);
    gap: 32px;
  }
}

.char-main,
.char-side {
  min-width: 0;
}

.char-notice {
  border: 1px solid $color-green-70;
  background: rgba(39, 144, 132, 0.08);
  padding: 10px 14px;
  margin-bottom: 24px;
}

.char-section {
  margin-bottom: 36px;

  h2 {
    @include font-title-regular;
    font-size: 15px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: $color-secondary;
    margin-bottom: 12px;
  }
}

.section-count {
  color: $color-text-hex-50;
  margin-left: 6px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;

  .btn-thin {
    @include font-title-regular;
    font-size: 12px;
  }
}

.item-rows {
  border-top: 1px solid $color-background-light-border;
}

.item-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 42px;
  padding: 4px 8px;
  border-bottom: 1px solid $color-background-light-border;

  &.selected {
    background: $color-background-light;
  }
}

.inventory-row,
.bag-content-row {
  grid-template-columns: 32px minmax(0, 1fr) auto;
}

.bag-content-row {
  padding-left: 32px;
  background: rgba(0, 0, 0, 0.15);
}

.slot-label {
  @include font-title-regular;
  font-size: 12px;
  text-transform: uppercase;
  color: $color-text-hex-50;
}

.item-name {
  @include font-text-regular;
  justify-self: start;
  text-align: left;
  background: none;
  border: 0;
  padding: 0;
  color: $color-text;
  overflow-wrap: anywhere;

  &:not(.static):hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
}

.item-empty {
  color: $color-text-hex-40;
}

.item-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: flex-end;

  .btn-thin {
    @include font-title-regular;
    font-size: 11px;
  }

  button[disabled] {
    opacity: 0.4;
    cursor: default;
  }
}

.put-select {
  background: $color-form-background;
  border: 1px solid $color-form-border;
  border-radius: 2px;
  color: $color-text-hex-70;
  font-size: 12px;
  padding: 3px 6px;
  max-width: 140px;
}

.bag-toggle {
  width: 32px;
}

.char-description {
  white-space: pre-line;
  line-height: 1.75;
  max-width: 64ch;
}

.description-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

.xp-bar {
  height: 6px;
  background: $color-background-light;
  border-radius: 3px;
  overflow: hidden;
}

.xp-fill {
  height: 100%;
  background: $color-secondary;
}

.xp-label {
  font-size: 13px;
  margin: 6px 0 0;
}

.char-danger {
  padding-top: 20px;
  border-top: 1px solid $color-background-light-border;

  p {
    font-size: 13px;
    margin-top: 8px;
  }
}
</style>
