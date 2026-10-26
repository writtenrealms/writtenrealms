<template>
  <main class="control-panel dashboard-view">
    <header class="dashboard-heading">
      <div><div class="dashboard-eyebrow"><Shield :size="15" aria-hidden="true" /> STAFF ADMINISTRATION</div><h1>Core Control Panel</h1><p>Platform activity, access, and operations.</p></div>
      <div class="control-status"><span v-if="accessMode" class="tag" :class="accessMode === 'open' ? 'tag-green' : 'tag-secondary'">{{ accessLabels[accessMode] }}</span><span v-else-if="!loading" class="tag">Access status unavailable</span><span v-if="accessMode && maintenance" class="tag tag-secondary">Maintenance</span><router-link to="/lobby" class="gray-link">View site <ArrowUpRight :size="14" aria-hidden="true" /></router-link></div>
    </header>
    <nav class="control-tabs" aria-label="Control panel sections">
      <button v-for="item in tabs" :key="item.id" :class="{selected:tab===item.id}" :aria-current="tab === item.id ? 'page' : undefined" @click="tab=item.id"><component :is="item.icon" :size="17" aria-hidden="true" />{{ item.label }}</button>
    </nav>
    <div v-show="tab==='overview'" class="dashboard-stack">
      <div class="control-toolbar"><div class="dashboard-note" role="status">{{ overview ? `Snapshot ${staffTime(overview.generated_at)} · cached up to ${overview.cache_seconds}s` : 'Platform overview' }}</div><div class="dashboard-actions"><label class="control-auto"><input v-model="autoRefresh" type="checkbox" /> Refresh every 30s</label><button class="btn-small button-gray" :disabled="loading" @click="loadOverview"><RefreshCw :size="13" aria-hidden="true" /> {{ loading ? 'REFRESHING…' : 'REFRESH' }}</button></div></div>
      <p v-if="error" class="inline-notice inline-notice-error" role="alert">{{ error }}</p>
      <div v-if="loading && !overview" class="loading-container" role="status" aria-label="Loading platform overview"><div class="spinner"></div></div>
      <StaffOverview v-if="overview" :data="overview" />
      <div v-if="overview" class="control-footer-links"><router-link :to="{name:'staff_worlds'}">Browse all worlds <ArrowUpRight :size="14" aria-hidden="true" /></router-link><router-link :to="{name:'staff_signups'}">Account directory <ArrowUpRight :size="14" aria-hidden="true" /></router-link></div>
    </div>
    <KeepAlive><StaffAccess v-if="tab==='access'" @changed="settingsChanged" /><StaffJobs v-else-if="tab==='jobs'" /><StaffAudit v-else-if="tab==='audit'" /></KeepAlive>
  </main>
</template>
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import axios from 'axios';
import { Activity, ArrowUpRight, History, ListTodo, RefreshCw, Shield, ShieldCheck } from '@lucide/vue';
import StaffOverview from '@/components/staff/StaffOverview.vue';
import StaffAccess from '@/components/staff/StaffAccess.vue';
import StaffJobs from '@/components/staff/StaffJobs.vue';
import StaffAudit from '@/components/staff/StaffAudit.vue';
import { accessLabels, staffError, staffTime, type AccessMode, type StaffAccessPolicy, type StaffOverview as Overview } from '@/core/staff';
const tab = ref('overview');
const autoRefresh = ref(false);
const overview = ref<Overview | null>(null);
const loading = ref(false);
const error = ref('');
const accessMode = ref<AccessMode | null>(null);
const maintenance = ref(false);
const tabs = [
  { id: 'overview', label: 'Overview', icon: Activity },
  { id: 'access', label: 'Access', icon: ShieldCheck },
  { id: 'jobs', label: 'Queues & jobs', icon: ListTodo },
  { id: 'audit', label: 'Admin history', icon: History },
];
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;

function settingsChanged(settings: StaffAccessPolicy & { maintenance_mode: boolean }) {
  accessMode.value = settings.access_mode;
  maintenance.value = settings.maintenance_mode;
}

async function loadOverview() {
  if (loading.value) return;
  loading.value = true;
  error.value = '';
  try {
    const [overviewResponse, settingsResponse] = await Promise.allSettled([
      axios.get('/staff/overview/'), axios.get('/staff/site/'),
    ]);
    if (disposed) return;
    if (settingsResponse.status === 'fulfilled') settingsChanged(settingsResponse.value.data);
    else accessMode.value = null;
    if (overviewResponse.status === 'rejected') throw overviewResponse.reason;
    overview.value = overviewResponse.value.data;
  } catch (requestError) {
    if (!disposed) {
      error.value = staffError(requestError,
        'Could not load the overview. Previously loaded values may be out of date. Try refreshing.');
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void loadOverview();
  // Poll only this cheap, cached overview. Worker inspection stays manual.
  timer = setInterval(() => {
    if (autoRefresh.value && tab.value === 'overview' && document.visibilityState === 'visible') {
      void loadOverview();
    }
  }, 30000);
});
onUnmounted(() => {
  disposed = true;
  if (timer) clearInterval(timer);
});
</script>
<style lang="scss">
@import '@/styles/staff.scss';
</style>
