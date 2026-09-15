<template>
  <section class="instance-completions" aria-labelledby="instance-completions-title" :aria-busy="loading">
    <h3 id="instance-completions-title">Instance Completions</h3>
    <p class="completion-help">Completed runs this character participated in. Clear times include pauses and absences.</p>
    <div v-if="completions.length" class="completion-table-wrap">
      <table>
        <thead>
          <tr><th scope="col">Instance</th><th scope="col">Completed</th><th scope="col">Clear time</th><th scope="col">Timing</th></tr>
        </thead>
        <tbody>
          <tr v-for="completion in completions" :key="completion.id">
            <td>{{ completion.template_name }}</td>
            <td><time :datetime="completion.completed_at">{{ formatDate(completion.completed_at) }}</time></td>
            <td class="clear-time">{{ formatClearTime(completion.clear_time_ms) }}</td>
            <td>{{ completion.time_control ? "Time control" : "Continuous" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!loading && !error" class="completion-empty">No instance completions recorded.</p>
    <p v-if="loading" role="status">Loading completions…</p>
    <div v-if="error" role="alert" class="completion-error">
      <span>Could not load instance completions.</span>
      <button class="btn-small" :disabled="loading" @click="$emit('load')">RETRY</button>
    </div>
    <button v-else-if="hasMore" class="btn-small completion-more" :disabled="loading" @click="$emit('load')">LOAD MORE</button>
  </section>
</template>

<script setup lang="ts">
import { formatClearTime, type PlayerCompletion } from "@/services/playerCompletions";

defineProps<{
  completions: PlayerCompletion[];
  loading: boolean;
  error: boolean;
  hasMore: boolean;
}>();
defineEmits<{ load: [] }>();

const formatDate = (value: string) => new Date(value).toLocaleString(undefined, {
  year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
});
</script>

<style scoped lang="scss">
.instance-completions { margin-top: 28px; }
.completion-help, .completion-empty { opacity: 0.7; margin: 8px 0 14px; }
.completion-table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 10px 16px 10px 0; border-bottom: 1px solid rgba(128, 128, 128, 0.25); }
th { font-size: 0.85em; opacity: 0.7; font-weight: 500; }
td:first-child { font-weight: 500; }
time, .clear-time { white-space: nowrap; }
.clear-time { font-variant-numeric: tabular-nums; }
.completion-more { margin-top: 14px; }
.completion-error { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
</style>
