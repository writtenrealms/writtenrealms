<template>
  <div class="leaderboard-panels" :class="{ 'is-tabbed': tabbed }" :aria-busy="loading">
    <div v-if="error" class="leaderboard-error" role="alert">
      <p>Leaderboards couldn’t be loaded.</p>
      <button class="btn-thin mt-2" :disabled="loading" @click="$emit('retry')">RETRY</button>
    </div>
    <p v-else-if="loading && !panels.length" class="color-text-60">Loading leaderboards…</p>
    <template v-if="tabbed">
      <div class="rankings-label">RANKINGS</div>
      <div class="ranking-tabs" role="tablist">
        <button v-for="panel in panels" :key="panel.id" :id="`ranking-${panel.id}`" role="tab"
          :aria-selected="panel.id === activeId" :aria-controls="`ranking-panel-${panel.id}`"
          @click="selectedId = panel.id">{{ panel.title }}</button>
      </div>
    </template>
    <section v-for="panel in panels" :key="panel.id" :id="`ranking-panel-${panel.id}`" class="leaderboard-panel"
      v-show="!tabbed || panel.id === activeId" :role="tabbed ? 'tabpanel' : undefined"
      :aria-labelledby="`ranking-${panel.id}`">
      <h2 v-if="!tabbed" :id="`ranking-${panel.id}`">{{ panel.title }}</h2>
      <p class="ranking-description color-text-60">{{ panel.description }}</p>
      <ol v-if="panel.entries.length" class="ranking-entries">
        <li v-for="(entry, index) in panel.entries" :key="entry.id" class="ranking-entry"
          :class="{ 'ranking-entry-own': isOwn(entry) }">
          <span class="ranking-position color-secondary">{{ index + 1 }}</span>
          <div class="ranking-character">
            <div>
              <span v-if="entry.is_party">{{ entry.name }}</span>
              <router-link v-else class="ranking-name" :to="{ name: 'character_details', params: { player_id: entry.id } }">{{ entry.name }}</router-link>
              <span v-if="isOwn(entry)" class="tag tag-secondary ranking-own-tag">YOURS</span>
            </div>
            <div v-if="entry.core_faction || entry.archetype" class="ranking-detail color-text-60">
              {{ [entry.core_faction, entry.archetype].filter(Boolean).join(' · ') }}
            </div>
          </div>
          <div class="ranking-score">
            <template v-if="panel.type === 'instance_clear_time'">{{ formatClearTime(entry.clear_time_ms ?? 0) }}</template>
            <template v-else-if="panel.type === 'dueling'">
              {{ (entry.win_percentage ?? 0).toFixed(1) }}%
              <div class="ranking-detail color-text-60">{{ entry.wins }}W · {{ entry.losses }}L</div>
            </template>
            <template v-else>
              {{ (entry.glory ?? 0).toLocaleString() }} glory
              <div class="ranking-detail color-text-60">{{ (entry.experience ?? 0).toLocaleString() }} XP</div>
            </template>
          </div>
        </li>
      </ol>
      <p v-else class="ranking-empty color-text-60">{{ panel.empty_message }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { formatClearTime } from "@/services/playerCompletions";

interface RankingEntry {
  id: number;
  name: string;
  core_faction?: string;
  archetype?: string;
  is_party?: boolean;
  clear_time_ms?: number;
  win_percentage?: number;
  wins?: number;
  losses?: number;
  glory?: number;
  experience?: number;
}
interface RankingPanel {
  id: string;
  type: string;
  title: string;
  description: string;
  empty_message: string;
  entries: RankingEntry[];
}
const props = defineProps<{ panels: RankingPanel[]; error?: boolean; loading?: boolean; ownPlayerIds?: number[] }>();
defineEmits<{ retry: [] }>();

// Several rankings share one column as tabs; a single ranking keeps its heading.
const tabbed = computed(() => props.panels.length > 1);
const selectedId = ref<string | null>(null);
const activeId = computed(() =>
  props.panels.some(panel => panel.id === selectedId.value) ? selectedId.value : props.panels[0]?.id);

// Party clear entries are records rather than characters.
const isOwn = (entry: RankingEntry) => !entry.is_party && !!props.ownPlayerIds?.includes(entry.id);
</script>

<style scoped lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.leaderboard-panels:not(.is-tabbed) .leaderboard-panel + .leaderboard-panel { margin-top: 2.5rem; }
.rankings-label {
  @include font-title-regular;
  color: $color-secondary;
  font-size: 15px;
  letter-spacing: 1.5px;
  line-height: 18px;
  margin-bottom: 12px;
}
.ranking-tabs {
  display: flex;
  flex-wrap: wrap;
  column-gap: 20px;
  border-bottom: 1px solid $color-background-light-border;
  margin-bottom: 12px;

  button {
    @include font-title-regular;
    background: none;
    border: 0;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    padding: 0 0 8px;
    font-size: 12px;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: $color-text-hex-60;

    &:hover { color: $color-text; }
    &[aria-selected="true"] { color: $color-text; border-bottom-color: $color-primary; }
  }
}
h2 {
  @include font-title-regular;
  color: $color-secondary;
  font-size: 15px;
  letter-spacing: 1px;
  line-height: 1.5;
  text-transform: uppercase;
  margin: 0 0 0.5rem;
}
.ranking-description, .ranking-detail, .ranking-empty { font-size: 13px; line-height: 1.6; }
.ranking-description { margin-bottom: 1rem; }
.ranking-entries { list-style: none; padding: 0; margin: 0; }
.ranking-entry { display: flex; gap: 0.6rem; padding: 0.6rem 0; align-items: baseline; border-bottom: 1px solid $color-background-border; }
.ranking-position { min-width: 1rem; }
.ranking-entry-own { background: linear-gradient(90deg, rgba(245, 201, 131, 0.06), transparent 70%); }
.ranking-own-tag { margin-left: 6px; vertical-align: 2px; }
.ranking-name { color: $color-text; }
.ranking-name:hover { color: $color-secondary; }
.ranking-character { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.ranking-score { text-align: right; font-variant-numeric: tabular-nums; font-size: 14px; }
.leaderboard-error { margin-bottom: 1.5rem; }
</style>
