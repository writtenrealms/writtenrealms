<template>
  <div class="leaderboard-panels" :aria-busy="loading">
    <div v-if="error" class="leaderboard-error" role="alert">
      <p>Leaderboards couldn’t be loaded.</p>
      <button class="btn-thin mt-2" :disabled="loading" @click="$emit('retry')">RETRY</button>
    </div>
    <p v-else-if="loading && !panels.length" class="color-text-60">Loading leaderboards…</p>
    <section v-for="panel in panels" :key="panel.id" class="leaderboard-panel" :aria-labelledby="`ranking-${panel.id}`">
      <h2 :id="`ranking-${panel.id}`">{{ panel.title }}</h2>
      <p class="ranking-description color-text-60">{{ panel.description }}</p>
      <ol v-if="panel.entries.length" class="ranking-entries">
        <li v-for="(entry, index) in panel.entries" :key="entry.id" class="ranking-entry">
          <span class="ranking-position color-secondary">{{ index + 1 }}</span>
          <div class="ranking-character">
            <div>{{ entry.name }}</div>
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
import { formatClearTime } from "@/services/playerCompletions";

interface RankingEntry {
  id: number;
  name: string;
  core_faction?: string;
  archetype?: string;
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
defineProps<{ panels: RankingPanel[]; error?: boolean; loading?: boolean }>();
defineEmits<{ retry: [] }>();
</script>

<style scoped lang="scss">
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.leaderboard-panel + .leaderboard-panel { margin-top: 2.5rem; }
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
.ranking-character { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.ranking-score { text-align: right; font-variant-numeric: tabular-nums; font-size: 14px; }
.leaderboard-error { margin-bottom: 1.5rem; }
</style>
