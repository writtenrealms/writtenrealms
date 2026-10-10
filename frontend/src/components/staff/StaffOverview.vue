<template>
  <div class="control-stack">
    <div class="metric-grid">
      <div class="metric-card"><span>Characters online</span><strong>{{ data.counts.online_characters.toLocaleString() }}</strong><small>{{ data.counts.online_users }} connected accounts</small></div>
      <div class="metric-card"><span>Running worlds</span><strong>{{ data.counts.running_worlds.toLocaleString() }}</strong><small>{{ data.counts.root_worlds }} authored · {{ data.counts.public_worlds }} public</small></div>
      <div class="metric-card"><span>World entrants · 24h</span><strong>{{ data.counts.active_users_24h.toLocaleString() }}</strong><small>Distinct accounts entering a world</small></div>
      <div class="metric-card"><span>New accounts · 24h</span><strong>{{ data.counts.signups_24h.toLocaleString() }}</strong><small>{{ data.counts.users.toLocaleString() }} accounts · {{ data.counts.characters.toLocaleString() }} characters</small></div>
    </div>

    <section class="panel control-section" aria-labelledby="activity-heading">
      <div class="control-section-heading">
        <div><h2 id="activity-heading">World entries &amp; signups</h2><p>Last 14 days · UTC · today is still in progress</p></div>
        <div class="chart-legend"><span><i class="chart-key chart-key-active"></i>World entrants</span><span><i class="chart-key chart-key-signups"></i>New accounts</span></div>
      </div>
      <div v-if="data.activity.length" class="activity-chart" role="img" :aria-label="chartSummary">
        <div v-for="day in data.activity" :key="day.date" class="activity-day" :title="`${day.date}: ${day.active_users} world entrants, ${day.signups} new accounts`">
          <div class="activity-bars"><div class="activity-bar activity-bar-active" :style="{height: chartHeight(day.active_users, chartMax)}"></div><div class="activity-bar activity-bar-signups" :style="{height: chartHeight(day.signups, chartMax)}"></div></div>
          <span>{{ day.date.slice(8) }}</span>
        </div>
      </div>
      <p v-if="!chartMax" class="control-empty">No signups or world entries recorded during this period.</p>
      <p class="control-note">World entrants are distinct accounts entering a world each day; this does not measure session duration or all player activity.</p>
      <details class="control-details"><summary>Daily counts</summary>
        <div class="data-table-scroll"><table class="data-table"><thead><tr><th>Date (UTC)</th><th>Unique world entrants</th><th>New accounts</th></tr></thead><tbody><tr v-for="day in data.activity" :key="day.date"><th>{{ day.date }}</th><td>{{ day.active_users }}</td><td>{{ day.signups }}</td></tr></tbody></table></div>
      </details>
    </section>

    <section class="panel control-section" aria-labelledby="worlds-heading">
      <div class="control-section-heading"><div><h2 id="worlds-heading">Online worlds</h2><p>Open world administration to manage lifecycle and maintenance.</p></div><span class="tag">{{ data.online_worlds.length }} shown</span></div>
      <div v-if="data.online_worlds.length" class="data-table-scroll"><table class="data-table control-table"><thead><tr><th>World</th><th>State</th><th>Characters</th><th>Last change</th><th></th></tr></thead><tbody>
        <tr v-for="world in data.online_worlds" :key="world.id"><th><router-link :to="{name:'builder_world_admin',params:{world_id:world.root_world_id}}">{{ world.name }}</router-link><small>#{{ world.id }}</small></th><td><span class="tag" :class="{'tag-green': world.state === 'running'}">{{ world.state }}</span><span v-if="world.maintenance_mode" class="tag tag-secondary">Maintenance</span></td><td>{{ world.online_characters }}</td><td>{{ staffTime(world.changed_at) }}</td><td><router-link :to="{name:'builder_world_admin_instance',params:{world_id:world.root_world_id,instance_id:world.id}}">Manage</router-link></td></tr>
      </tbody></table></div>
      <p v-else class="control-empty">No worlds are currently online.</p>
      <p v-if="data.online_worlds.length >= data.limits.online_worlds" class="control-note">Showing the first {{ data.limits.online_worlds }} worlds. The totals above include all worlds.</p>
    </section>

    <section class="panel control-section" aria-labelledby="characters-heading">
      <div class="control-section-heading"><div><h2 id="characters-heading">Online characters</h2><p>Connected players in running worlds. Counts can briefly lag disconnection cleanup.</p></div><span class="tag">{{ data.counts.online_characters }} total</span></div>
      <div v-if="data.online_characters.length" class="data-table-scroll"><table class="data-table control-table"><thead><tr><th>Character</th><th>Account</th><th>World</th><th>Connected</th><th>Last action</th></tr></thead><tbody>
        <tr v-for="character in data.online_characters" :key="character.id"><th><router-link :to="{name:'builder_world_player_details',params:{world_id:character.root_world_id,player_id:character.id}}">{{ character.name }}</router-link><small>Level {{ character.level }} · #{{ character.id }}</small></th><td><router-link :to="{name:'staff_user_info',params:{user_id:character.user_id}}">{{ character.username || `Account #${character.user_id}` }}</router-link></td><td><router-link :to="{name:'builder_world_admin',params:{world_id:character.root_world_id}}">{{ character.world_name }}</router-link><small>#{{ character.world_id }}</small></td><td>{{ staffTime(character.last_connection_ts) }}</td><td>{{ staffTime(character.last_action_ts) }}</td></tr>
      </tbody></table></div>
      <p v-else class="control-empty">No characters are currently connected.</p>
      <p v-if="data.counts.online_characters > data.online_characters.length" class="control-note">Showing {{ data.online_characters.length }} of {{ data.counts.online_characters }} online characters.</p>
    </section>
  </div>
</template>
<script lang="ts" setup>
import { computed } from 'vue';
import { chartHeight, staffTime, type StaffOverview } from '@/core/staff';
const props = defineProps<{data: StaffOverview}>();
const chartMax = computed(() => Math.max(0, ...props.data.activity.flatMap(day => [day.signups, day.active_users])));
const chartSummary = computed(() => `World entries and signups for ${props.data.activity.length} days. ${props.data.activity.map(day => `${day.date}: ${day.active_users} world entrants, ${day.signups} new`).join('; ')}`);
</script>
