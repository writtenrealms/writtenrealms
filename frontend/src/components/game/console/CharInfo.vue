<template>
  <div>
    <div class="name">{{ capfirst(char.name) }}</div>
    <div class="summary" v-if="world.allow_combat">
      Level {{ char.level }} {{ gender }} {{ char.archetype }}
      <template v-if="char.is_elite">
        &nbsp;[&nbsp;<span class="color-primary">elite</span>&nbsp;]
      </template>
    </div>
    <div class="health color-text-50" v-if="world.allow_combat">
      Health: {{ char.health }} / {{ char.health_max }}
    </div>

    <div class="description" v-if="descLines.length">
      <div class="description-line" v-for="(line, index) in descLines" :key="index">{{ line }}</div>
    </div>

    <!-- <div class="description" v-if="char.description">
      {{ char.description }}
    </div> -->

    <div class="equipment" v-if="slots && slots.length">
      <div>{{ capfirst(char.name) }} is using:</div>
      <div v-for="slot in slots" :key="slot.slotItem.key">
        <span class="font-text-regular">{{ slot.slotName }}</span>:
        <span v-if="isLastMessage && slot.slotItemIsCurrent"
          :class="{ ['color-secondary']: slot.slotItemIsMagic }"
          class='interactive'
          v-interactive="{
            target: slot.slotItem,
            primaryAction: slot.slotItemIsPlayerOwned,
            actionContext: slot.slotItemIsPlayerOwned
              ? 'equipment'
              : undefined,
          }"
        >{{ slot.slotItemName }}</span>
        <span v-else :class="{ ['color-secondary']: slot.slotItemIsMagic }">{{ slot.slotItemName }}</span>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { EQUIPMENT_SLOT_LIST } from "@/constants";
import { capfirst } from "@/core/utils.ts";

const props = defineProps<{
  char: any;
  message: any;
  isLastMessage: any;
}>();

const store = useStore();

const playerEquipmentKeys = computed(() => new Set(
  Object.values(store.state.game.player?.equipment || {})
    .filter(Boolean)
    .map((item: any) => item.key),
));
const isPlayerChar = computed(
  () => props.char.key === store.state.game.player?.key,
);

const slots = computed(() => {
  const slots: any[] = [];
  for (const slot of EQUIPMENT_SLOT_LIST) {
    const eq = props.char.equipment;
    if (eq && eq[slot]) {
      slots.push({
        slotName: capfirst(slot),
        slotItemName: eq[slot].name,
        slotItemIsMagic: eq[slot].is_magic,
        slotItem: eq[slot],
        slotItemIsPlayerOwned: playerEquipmentKeys.value.has(eq[slot].key),
        slotItemIsCurrent: (
          !isPlayerChar.value ||
          playerEquipmentKeys.value.has(eq[slot].key)
        ),
      });
    }
  }
  return slots;
});


const gender = computed(() => {
  if (props.char.gender == "non_binary") return "non-binary";
  return props.char.gender
});

const world = computed(() => store.state.game.world);

const descLines = computed(() => {
  if (props.char.description) return props.char.description.split('\n');
  return [];
});
</script>
