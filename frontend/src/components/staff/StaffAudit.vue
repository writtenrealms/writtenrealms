<template>
  <section class="panel control-section">
    <div class="control-section-heading"><div><h2>Administrator activity</h2><p>Recorded changes to platform settings, email access, and recovery jobs.</p></div><button class="btn-small button-gray" :disabled="loading" @click="load(0)">{{ loading ? 'LOADING…' : 'REFRESH HISTORY' }}</button></div>
    <p v-if="error" class="inline-notice inline-notice-error" role="alert">{{ error }}</p>
    <div v-if="data.results.length" class="data-table-scroll"><table class="data-table control-table"><thead><tr><th>When</th><th>Administrator</th><th>Action</th><th>Details</th></tr></thead><tbody><tr v-for="entry in data.results" :key="entry.id"><td>{{ staffTime(entry.created_at) }}</td><td>{{ entry.actor || 'Deleted account' }}</td><th>{{ entry.action.replace(/_/g,' ') }}<small>{{ entry.target }}</small></th><td><details v-if="Object.keys(entry.details).length"><summary>View change</summary><pre class="audit-details">{{ JSON.stringify(entry.details,null,2) }}</pre></details><span v-else>—</span></td></tr></tbody></table></div>
    <p v-else-if="!loading && !error" class="control-empty">No administrator actions have been recorded yet.</p>
    <p v-else-if="loading" class="control-empty" role="status">Loading administrator history…</p>
    <div v-if="data.count > data.limit" class="control-pagination"><button class="btn-small button-gray" :disabled="loading || data.offset === 0" @click="load(Math.max(0,data.offset-data.limit))">NEWER</button><span>{{ data.offset+1 }}–{{ Math.min(data.count,data.offset+data.results.length) }} of {{ data.count }}</span><button class="btn-small button-gray" :disabled="loading || data.offset+data.limit >= data.count" @click="load(data.offset+data.limit)">OLDER</button></div>
  </section>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue';
import axios from 'axios';
import { staffTime, staffError, type StaffAudit } from '@/core/staff';
const data=ref({results:[] as (StaffAudit & {target:string})[],count:0,offset:0,limit:50}),loading=ref(false),error=ref('');
async function load(offset=0){if(loading.value)return;loading.value=true;error.value='';try{data.value=(await axios.get('/staff/audit/',{params:{offset}})).data;}catch(e){error.value=staffError(e,'Could not load administrator history.');}finally{loading.value=false;}}
onMounted(()=>load());
</script>
