<template>
  <div class="dashboard-view dashboard-stack">
    <header class="dashboard-heading">
      <div>
        <router-link :to="mobPath" class="dashboard-note">← Mob details</router-link>
        <div class="dashboard-eyebrow"><Bug :size="15" aria-hidden="true" /> STAFF DEBUG</div>
        <h1>{{ debug.data.value?.mob.name || 'Mob' }} · Debug</h1>
        <p>World #{{ route.params.instance_id }} · mob.{{ route.params.mob_id }}</p>
      </div>
      <button class="btn-small button-gray" :disabled="debug.loading.value" @click="refresh"><RefreshCw :size="13" aria-hidden="true" /> REFRESH</button>
    </header>
    <p v-if="debug.loading.value" class="dashboard-empty" role="status">Loading debug data…</p>
    <p v-else-if="debug.error.value" role="alert" class="inline-notice inline-notice-error">{{ debug.error.value }}</p>
    <template v-else-if="debug.data.value">
      <ConversationFailure :failure="debug.data.value.conversation_failure || null" />
      <section class="panel dashboard-section">
        <div class="dashboard-section-heading"><div><h2>LLM payload preview</h2><p>Reconstruct the request body using the current mob, room, policy, and model configuration.</p></div><span class="tag tag-secondary">Preview only</span></div>
        <p class="inline-notice">This does not call the LLM, spend budget, store a request, or advance a conversation. It is a hypothetical next reply, not a record of an earlier request.</p>
        <p v-if="!debug.data.value.mob.conversation.configured" class="dashboard-empty">This mob definition has no LLM conversation entry. Its raw records are available below.</p>
        <form v-else class="preview-form" @submit.prevent="buildPreview">
          <div class="form-group">
            <label for="preview-speech">What would the player say?</label>
            <textarea id="preview-speech" v-model="speech" rows="3" maxlength="280" required placeholder="Can an agreed price still be unfair?" aria-describedby="preview-context" />
          </div>
          <div class="dashboard-actions"><label class="preview-checkbox"><input v-model="useActive" type="checkbox" /> Use active conversation, if available</label><span class="dashboard-note">{{ speech.length }}/280</span></div>
          <p id="preview-context" class="dashboard-note">Otherwise, preview a new conversation after the authored greeting. Live gameplay still checks player eligibility, authored interactions, cooldowns, and budget before sending.</p>
          <button class="btn-small" :disabled="preview.loading.value || !speech.trim()">{{ preview.loading.value ? 'BUILDING…' : 'BUILD PAYLOAD PREVIEW' }}</button>
        </form>
        <p v-if="preview.error.value" class="inline-notice inline-notice-error" role="alert">{{ preview.error.value }}</p>
      </section>
      <section v-if="preview.data.value" class="dashboard-stack" aria-label="Payload preview result" aria-live="polite">
        <div class="panel dashboard-section">
          <div class="dashboard-section-heading"><h2>Reconstructed request</h2><span class="tag">Not sent</span></div>
          <InspectionFacts :values="{ provider: preview.data.value.provider, model: preview.data.value.model,
            context: preview.data.value.context === 'active_conversation' ? 'Current conversation transcript' : 'New conversation with authored greeting',
            integration_enabled: preview.data.value.integration_enabled, world_conversations_enabled: preview.data.value.world_enabled,
            generated: new Date(preview.data.value.generated_at).toLocaleString() }" />
          <p v-if="preview.data.value.pending_reply" class="inline-notice">A reply is already pending. This preview does not represent another request that gameplay would accept right now.</p>
          <p v-if="preview.data.value.unavailable_reason" class="inline-notice">{{ preview.data.value.unavailable_reason }}</p>
        </div>
        <JsonInspector v-if="preview.data.value.provider_body" title="Provider request body" :value="preview.data.value.provider_body" open />
        <JsonInspector title="Knowledge packet" :value="preview.data.value.knowledge_packet" />
        <JsonInspector title="System instructions" :value="preview.data.value.instructions" />
      </section>
      <section class="dashboard-stack" aria-label="Current raw records">
        <div><h2>Current raw records</h2><p class="dashboard-note">Read at {{ new Date(debug.data.value.inspected_at).toLocaleString() }}. Refresh to load changes.</p></div>
        <JsonInspector title="Runtime mob" :value="debug.data.value.mob.runtime" />
        <JsonInspector title="Mob definition" :value="debug.data.value.mob.definition_record" />
        <JsonInspector title="Character state" :value="debug.data.value.mob.state" />
        <JsonInspector title="Spawn placement" :value="debug.data.value.mob.spawn" />
        <JsonInspector title="World dialogue knowledge" :value="debug.data.value.world_conversation" />
        <JsonInspector title="World conversation enablement" :value="debug.data.value.policy" />
        <JsonInspector title="Conversation focus and transcript" :value="debug.data.value.focus" />
        <JsonInspector title="Combat participation" :value="debug.data.value.combat" />
        <JsonInspector title="Inventory, equipment, effects, factions, and services" :value="relatedRecords" />
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { Bug, RefreshCw } from '@lucide/vue';
import JsonInspector from '@/components/builder/JsonInspector.vue';
import InspectionFacts from '@/components/builder/InspectionFacts.vue';
import ConversationFailure from '@/components/builder/ConversationFailure.vue';
import { useInspectionRequest } from '@/composables/useInspectionRequest';
const route = useRoute();
const mobPath = computed(() => `/build/worlds/${route.params.world_id}/admin/${route.params.instance_id}/mobs/${route.params.mob_id}`);
const api = computed(() => `/builder/worlds/${route.params.world_id}/admin/instance/${route.params.instance_id}/mobs/${route.params.mob_id}/debug/`);
const debug = useInspectionRequest();
const preview = useInspectionRequest();
const speech = ref('');
const useActive = ref(true);
const relatedRecords = computed(() => Object.fromEntries(['inventory', 'equipment', 'effects', 'effects_truncated', 'factions', 'factions_truncated', 'services'].map(k => [k, debug.data.value?.mob[k]])));
function refresh() { preview.clear(); void debug.load(api.value); }
function buildPreview() { return preview.load(api.value, { method: 'post', data: { speech: speech.value, use_active_conversation: useActive.value } }); }
watch([speech, useActive], preview.clear);
watch(api, () => { speech.value = ''; useActive.value = true; refresh(); }, { immediate: true });
</script>

<style scoped lang="scss">
.preview-form { display: flex; flex-direction: column; align-items: flex-start; gap: 0.8rem; margin-top: 1.25rem; .form-group { width: 100%; min-width: 0; margin: 0; } label { white-space: normal; } textarea { min-height: 6rem; } }
.preview-checkbox { display: flex; align-items: center; gap: 0.5rem; input { margin: 0; } }
</style>
