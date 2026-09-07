<template>
  <div class="game-settings">
    <h1>SETTINGS</h1>
    <section v-if="control?.enabled" class="time-settings" aria-labelledby="time-settings-title">
      <h2 id="time-settings-title">Time Control</h2>
      <CombatPauseToggle />
      <p>When checked, combat waits for your approval before every round and the whole instance pauses while you think. Exploration uses normal world timing.</p>
      <p>This checkbox applies immediately. Uncheck to resume normal timing. You can also type <code>pause</code> or <code>resume</code>.</p>
    </section>

    <EditEntity title="Preferences" :schema="schema" :data="preferences" action="game/save_player_config" @close="emit('close')" />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useStore } from "vuex";
import EditEntity from "@/components/forms/EditEntity.vue";
import type { FormElement } from "@/core/forms";
import type { InstanceTimeControl } from "@/core/instanceTimeControl";
import CombatPauseToggle from "@/components/game/CombatPauseToggle.vue";

const store = useStore();
const emit = defineEmits(["close"]);
const control = computed<InstanceTimeControl | null>(() => store.state.game.instance_time_control);
const preferences = computed(() => ({ ...store.state.game.player_config }));
const schema = computed<FormElement[]>(() => {
  const fields: FormElement[] = [
    { attr: "room_brief", label: "Room Brief Mode", widget: "checkbox", help: "Show full room descriptions only when using look." },
    { attr: "combat_brief", label: "Combat Brief Mode", widget: "checkbox", help: "Abbreviate and indent combat text for easier scanning." },
    { attr: "display_chat", label: "Display Chat", widget: "checkbox", help: "Show chat messages." },
  ];
  if (store.state.game.world?.is_multiplayer && (store.state.game.is_mobile || store.state.game.player?.is_builder)) {
    fields.push({ attr: "idle_logout", label: "Idle Auto-Logout", widget: "checkbox", help: "Automatically log out after a period of inactivity." });
  }
  if (store.state.game.is_mobile) fields.push({ attr: "mobile_map_width", label: "Map Width" });
  return fields.filter(field => preferences.value[field.attr] !== undefined);
});
</script>

<style scoped lang="scss">
@import "@/styles/colors.scss";
.game-settings { padding: 20px; max-height: 90vh; }
.time-settings { margin-top: 18px; padding-bottom: 22px; border-bottom: 1px solid $color-background-border; }
.time-settings p { color: $color-text-hex-70; margin: 10px 0; line-height: 1.5; }
:deep(.edit-entity) { padding: 20px 0 0 !important; }
</style>
