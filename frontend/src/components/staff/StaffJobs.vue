<template>
  <div class="dashboard-stack">
    <div class="dashboard-section-heading"><div><h2>Queues &amp; jobs</h2><p>Inspected on request. {{ data ? `Snapshot ${staffTime(data.generated_at)}.` : '' }}</p></div><button class="btn-small button-gray" :disabled="loading" @click="load">{{ loading ? 'INSPECTING…' : 'REFRESH JOBS' }}</button></div>
    <p v-if="error" class="inline-notice inline-notice-error" role="alert">{{ error }}</p>
    <p v-if="success" class="inline-notice" role="status">{{ success }}</p>
    <p v-if="loading && !data" class="dashboard-empty" role="status">Inspecting the broker and workers…</p>
    <template v-if="data">
      <section class="panel dashboard-section"><div class="dashboard-section-heading"><div><h2>Broker queues</h2><p>Ready messages are waiting to be delivered to a worker.</p></div><span class="tag" :class="{'tag-green':data.broker.status === 'ok','tag-secondary':data.broker.status !== 'ok'}">{{ data.broker.status }}</span></div>
        <p v-if="data.broker.detail" class="dashboard-note">{{ data.broker.detail }}</p>
        <div v-if="data.broker.queues.length" class="data-table-scroll"><table class="data-table dashboard-table"><thead><tr><th>Queue</th><th>Ready</th><th>Consumers</th><th>Unacknowledged</th></tr></thead><tbody><tr v-for="queue in data.broker.queues" :key="queue.name"><th>{{ queue.name }}</th><td>{{ queue.ready ?? 'Unavailable' }}</td><td>{{ queue.consumers ?? 'Unavailable' }}</td><td>{{ queue.unacknowledged ?? 'Unavailable' }}</td></tr></tbody></table></div>
        <p v-else class="dashboard-empty">Queue counts are unavailable.</p>
      </section>
      <section class="panel dashboard-section"><div class="dashboard-section-heading"><div><h2>Workers</h2><p>Active tasks are executing; reserved tasks are waiting inside a worker.</p></div><span class="tag" :class="{'tag-green':data.workers.status === 'ok','tag-secondary':data.workers.status !== 'ok'}">{{ data.workers.status }}</span></div>
        <p v-if="data.workers.detail" class="dashboard-note">{{ data.workers.detail }}</p>
        <div v-for="worker in data.workers.items" :key="worker.name" class="worker-row"><div class="dashboard-section-heading"><strong>{{ worker.name }}</strong><span class="dashboard-note">{{ worker.active ?? 'Unavailable' }} active · {{ worker.reserved ?? 'Unavailable' }} reserved · {{ worker.scheduled ?? 'Unavailable' }} scheduled</span></div>
          <div v-if="worker.tasks.length" class="data-table-scroll"><table class="data-table dashboard-table"><thead><tr><th>Task</th><th>State</th><th>Started / ETA</th></tr></thead><tbody><tr v-for="task in worker.tasks" :key="`${task.id}-${task.state}`"><th>{{ task.name }}<small>{{ task.id }}</small></th><td><span class="tag">{{ task.state }}</span></td><td>{{ staffTime(task.eta || task.started_at) }}</td></tr></tbody></table></div>
          <p v-else class="dashboard-note">No tasks reported by this worker.</p><p v-if="worker.tasks_truncated" class="dashboard-note">Task list truncated. Worker totals include all reported tasks.</p>
        </div><p v-if="!data.workers.items.length" class="dashboard-empty">No worker replied to this inspection.</p>
      </section>
      <section class="panel dashboard-section"><div class="dashboard-section-heading"><div><h2>Durable work</h2><p>Database-backed work survives worker restarts.</p></div></div>
        <div class="data-table-scroll"><table class="data-table dashboard-table"><thead><tr><th>Work</th><th>Pending</th><th>Due now</th><th>Failed / retrying</th></tr></thead><tbody><tr><th>Scheduled triggers</th><td>{{ data.durable.scheduled_triggers.pending }}</td><td>{{ data.durable.scheduled_triggers.due }}</td><td>{{ data.durable.scheduled_triggers.failed }} failed</td></tr><tr><th>Prepared actions</th><td>{{ data.durable.prepared_actions.pending }}</td><td>{{ data.durable.prepared_actions.due }}</td><td>{{ data.durable.prepared_actions.failed }} failed</td></tr><tr><th>Event outbox</th><td>{{ data.durable.event_outbox.pending }}</td><td>{{ data.durable.event_outbox.due }} batches</td><td>{{ data.durable.event_outbox.retrying }} retrying</td></tr></tbody></table></div>
        <p v-if="data.durable.detail" class="dashboard-note">{{ data.durable.detail }}</p>
        <p v-if="data.durable.event_outbox.oldest_due_at" class="dashboard-note">Oldest due event: {{ staffTime(data.durable.event_outbox.oldest_due_at) }}</p>
        <details v-if="failures.length" class="control-details"><summary>Recent failures ({{ failures.length }})</summary><div class="data-table-scroll"><table class="data-table dashboard-table"><thead><tr><th>Type / ID</th><th>World</th><th>Failure</th><th>Time</th></tr></thead><tbody><tr v-for="failure in failures" :key="`${failure.type}-${failure.id}`"><th>{{ failure.type }} #{{ failure.id }}</th><td>{{ failure.world_id }}</td><td>{{ failure.failure_code }}</td><td>{{ staffTime(failure.completed_at) }}</td></tr></tbody></table></div></details>
      </section>
      <section class="panel dashboard-section"><div class="dashboard-section-heading"><div><h2>Recovery actions</h2><p>Enqueue a bounded recovery pass for overdue work. Submission does not mean completion.</p></div></div>
        <div v-for="action in data.actions" :key="action.id" class="control-job-action"><div><strong>{{ action.label }}</strong><p class="dashboard-note">{{ action.description }}</p></div><button class="btn-small" :disabled="running !== null" @click="run(action)">{{ running === action.id ? 'SUBMITTING…' : 'RUN NOW' }}</button></div>
      </section>
      <section class="panel dashboard-section"><div class="dashboard-section-heading"><div><h2>Periodic schedule</h2><p>Configured schedule; this does not confirm that the scheduler is running.</p></div></div><div v-if="data.periodic.length" class="data-table-scroll"><table class="data-table dashboard-table"><thead><tr><th>Job</th><th>Schedule</th></tr></thead><tbody><tr v-for="job in data.periodic" :key="job.id"><th>{{ job.id }}<small>{{ job.task }}</small></th><td>{{ job.schedule }}</td></tr></tbody></table></div><p v-else class="dashboard-empty">No periodic jobs are configured.</p></section>
    </template>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import axios from 'axios';
import { staffError, staffTime, type StaffJobs } from '@/core/staff';
const data=ref<StaffJobs|null>(null),loading=ref(false),error=ref(''),success=ref(''),running=ref<string|null>(null);
const failures=computed(()=>data.value ? [...data.value.durable.scheduled_triggers.recent_failures.map(row=>({...row,type:'Trigger'})),...data.value.durable.prepared_actions.recent_failures.map(row=>({...row,type:'Action'}))]:[]);
async function load(){if(loading.value)return;loading.value=true;error.value='';try{data.value=(await axios.get('/staff/jobs/')).data;}catch(e){error.value=staffError(e,'Could not inspect jobs. Existing values may be out of date.');}finally{loading.value=false;}}
async function run(action:StaffJobs['actions'][number]){if(running.value || !window.confirm(`Run ${action.label.toLowerCase()} now? ${action.description}`))return;running.value=action.id;error.value='';success.value='';try{const response=await axios.post('/staff/jobs/',{action:action.id,confirmed:true});success.value=`${action.label} queued. Task ID: ${response.data.task_id}. Refresh to inspect workers; task completion is not recorded here.`;}catch(e){error.value=staffError(e,'Could not enqueue this recovery action.');}finally{running.value=null;}}
onMounted(load);
</script>
