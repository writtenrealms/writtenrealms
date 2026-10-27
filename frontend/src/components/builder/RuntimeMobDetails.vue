<template>
  <div class="dashboard-stack">
    <section class="panel dashboard-section">
      <div class="dashboard-section-heading">
        <div><h2>{{ mob.name }}</h2><p>{{ mob.key }} · Level {{ mob.level }} {{ mob.type }}</p></div>
        <router-link v-if="staff" :to="`${path}/debug`" role="button" class="btn-small button-gray"><Bug :size="13" aria-hidden="true" /> DEBUG</router-link>
      </div>
      <p v-if="mob.pending_deletion" class="inline-notice">This mob is pending deletion. Its data may disappear on refresh.</p>
      <p class="mob-description">{{ mob.runtime.description || 'No description.' }}</p>
      <InspectionFacts :values="{ room: `${mob.room.name} · room@${mob.room.relative_id}`, zone: mob.room.zone,
        definition: mob.definition?.slug, room_description: mob.runtime.room_description,
        keywords: mob.runtime.keywords, state: mob.state.data }" />
    </section>
    <section class="panel dashboard-section">
      <div class="dashboard-section-heading"><h2>Conversation</h2><span class="tag" :class="mob.conversation.configured ? 'tag-secondary' : ''">{{ mob.conversation.configured ? 'Configured' : 'Not configured' }}</span></div>
      <template v-if="mob.conversation.configured">
        <p class="dashboard-note">From the mob definition’s conversation section. TALK opens with this greeting; a subsequent SAY can request an LLM reply.</p>
        <blockquote class="mob-greeting">{{ mob.conversation.greeting }}</blockquote>
        <h3>Approved knowledge</h3><p class="mob-description">{{ mob.conversation.knowledge || 'No additional knowledge.' }}</p>
        <InspectionFacts :values="{ world_conversations_enabled: mob.conversation.world_enabled,
          supports_conversation: mob.conversation.supported, conditions: mob.conversation.when }" />
      </template>
      <p v-else class="dashboard-empty">No LLM conversation entry is configured on this mob definition. Authored TALK interactions may still apply.</p>
    </section>
    <section class="panel dashboard-section">
      <div class="dashboard-section-heading"><h2>Vitals and attributes</h2></div>
      <InspectionFacts :values="pick(['health', 'health_max', 'energy', 'energy_max', 'stamina', 'stamina_max', 'health_regen', 'energy_regen', 'stamina_regen', 'attributes', 'armor', 'dodge', 'critical', 'resilience', 'attack_power', 'ability_power', 'weapon_damage'])" />
    </section>
    <section class="panel dashboard-section">
      <div class="dashboard-section-heading"><h2>Behavior and services</h2></div>
      <InspectionFacts :values="{ ...pick(['attackable', 'talkable', 'aggression', 'fights_back', 'invisible', 'alignment', 'roaming_type', 'roam_chance', 'traits', 'flags', 'combat_script', 'ability_cooldowns']), services: mob.services, factions: mob.factions }" />
      <p v-if="mob.factions_truncated" class="dashboard-note">Showing the first 100 faction assignments.</p>
    </section>
    <section class="panel dashboard-section">
      <div class="dashboard-section-heading"><h2>Inventory and equipment</h2></div>
      <ul v-if="mob.inventory.items.length" class="mob-items"><li v-for="item in mob.inventory.items" :key="item.id">{{ item.name }} <span class="dashboard-note">#{{ item.id }} · {{ item.type }} · Level {{ item.level }}{{ item.is_pending_deletion ? ' · Pending deletion' : '' }}</span></li></ul>
      <p v-else class="dashboard-empty">No carried items.</p>
      <div class="dashboard-actions">
        <button v-if="mob.inventory.after" class="btn-small button-gray" @click="$emit('inventoryNext', 0)">FIRST 100 CARRIED ITEMS</button>
        <button v-if="mob.inventory.next_after" class="btn-small button-gray" @click="$emit('inventoryNext', mob.inventory.next_after)">NEXT 100 CARRIED ITEMS</button>
      </div>
      <h3>Equipped items</h3>
      <ul v-if="mob.equipment.items.length" class="mob-items"><li v-for="item in mob.equipment.items" :key="item.id">{{ item.name }} <span class="dashboard-note">#{{ item.id }} · Level {{ item.level }}</span></li></ul>
      <p v-else class="dashboard-empty">Nothing equipped.</p>
      <p v-if="mob.equipment.truncated" class="dashboard-note">Showing the first 100 equipped items.</p>
    </section>
    <section class="panel dashboard-section">
      <div class="dashboard-section-heading"><h2>Spawn and effects</h2></div>
      <InspectionFacts :values="{ spawn_plan: mob.spawn?.plan?.name, spawn_entry: mob.spawn?.placement?.entry_slug,
        spawn_slot: mob.spawn?.placement?.slot, loot: mob.runtime.loot, effects: mob.effects }" />
      <p v-if="mob.effects_truncated" class="dashboard-note">Showing the first 100 effects.</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { Bug } from '@lucide/vue';
import InspectionFacts from './InspectionFacts.vue';
const props = defineProps<{ mob: any; path: string; staff: boolean }>();
defineEmits<{ inventoryNext: [after: number] }>();
function pick(keys: string[]) {
  return Object.fromEntries(keys.filter(key => key in props.mob.runtime).map(key => [key, props.mob.runtime[key]]));
}
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';
.mob-description { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0 0 1rem; }
.mob-greeting { margin: 1rem 0; padding-left: 1rem; border-left: 2px solid $color-secondary; white-space: pre-wrap; overflow-wrap: anywhere; }
h3 { font-size: 13px; color: $color-text-hex-60; margin-bottom: 0.5rem; }
.mob-items { padding-left: 1.2rem; overflow-wrap: anywhere; li { margin-bottom: 0.5rem; } }
</style>
