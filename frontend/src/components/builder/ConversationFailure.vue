<template>
  <section class="panel dashboard-section" aria-label="Latest dialogue failure">
    <div class="dashboard-section-heading"><h2>Latest dialogue failure</h2><span v-if="failure" class="tag tag-red">Not delivered</span></div>
    <p class="dashboard-note">Only the latest technical failure for this loaded mob is kept, until another failure replaces it or the mob is removed. Prompts are not captured. Refresh to check for a new failure.</p>
    <template v-if="failure">
      <p class="inline-notice">{{ explanation }}</p>
      <InspectionFacts :values="{ reason: failure.reason, occurred: new Date(failure.occurred_at).toLocaleString(), request_id: failure.request_id }" />
      <JsonInspector title="Failure details" :value="failure.details" open />
      <JsonInspector v-if="failure.response !== null" title="Rejected response" :value="failure.response" open />
      <p v-else class="dashboard-note">No returned response was available for this failure.</p>
      <p v-if="failure.response_truncated" class="inline-notice">The response exceeded the diagnostic limit. Only its first 4,096 characters are shown.</p>
    </template>
    <p v-else class="dashboard-empty">No technical failure has been recorded for this loaded mob. Responses erased before this feature was installed cannot be recovered.</p>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import InspectionFacts from './InspectionFacts.vue';
import JsonInspector from './JsonInspector.vue';
type Failure = { reason: string; occurred_at: string; request_id: string | null; details: Record<string, unknown>; response: string | null; response_truncated: boolean };
const props = defineProps<{ failure: Failure | null }>();
const explanations: Record<string, string> = {
  forbidden_speech_characters: 'The reply contains markup or control characters that cannot be spoken. Details identify their zero-based positions and Unicode code points.',
  speech_too_long: 'The reply exceeds the 280-character speech limit.',
  empty_speech_text: 'The reply contains no spoken text.',
  missing_speech_text: 'The response does not contain a text string.',
  reply_timed_out: 'The reply was not delivered before the conversation deadline.',
  invalid_json: 'The provider returned text that could not be parsed as JSON.',
  invalid_response_fields: 'The returned JSON must contain exactly one field named text.',
  invalid_text_type: 'The returned text field is not a string.',
  provider_text_length: 'The returned text field is empty or exceeds the provider adapter’s 4,096-character limit.',
  provider_refusal: 'The provider returned a refusal instead of the requested dialogue response.',
  provider_incomplete_output: 'The provider did not finish generating the response.',
};
const explanation = computed(() => explanations[props.failure?.reason || ''] || 'The conversation could not continue because of a technical problem. The reason code and details identify the failed check.');
</script>
