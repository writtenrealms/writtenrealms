<template>
  <div class="equipment">
    <div>You are using:</div>
    <table>
      <template v-if="slots.length">
        <tr v-for="(slot, index) in slots" :key="index">
          <td>{{slot.slotName}}</td>

          <td class="eq-item">
            <span
              v-if="isLastMessage && isCurrentEquipmentItem(slot.slotItem)"
              v-interactive="{
                target: slot.slotItem,
                primaryAction: true,
                actionContext: 'equipment',
              }"
              :key="slot.slotItem.key + 'interactive'"
              class='interactive'
              :class="[slot.slotItemQuality]"
            >{{ slot.slotItemName }}</span>
            <span v-else
              :key="slot.slotItem.key"
              :class="[slot.slotItemQuality]">{{ slot.slotItemName }}</span>
          </td>
        </tr>
      </template>
      <div v-else>Nothing.</div>
    </table>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import { EQUIPMENT_SLOT_LIST } from "@/constants.ts";

const store = useStore();
const props = defineProps<{message: any}>();

const currentEquipmentKeys = computed(() => new Set(
  Object.values(store.state.game.player?.equipment || {})
    .filter(Boolean)
    .map((item: any) => item.key),
));
const isCurrentEquipmentItem = (item: any) => (
  currentEquipmentKeys.value.has(item.key)
);

const isLastMessage = computed(() => store.state.game.last_message[props.message.type] == props.message);

const slots: any = computed(() => {
  var slots: {}[] = [];
  for (let slotName of EQUIPMENT_SLOT_LIST) {
    var slotDisplayName;
    if (slotName === "weapon") {
      slotDisplayName = "as " + slotName;
    } else if (slotName === "offhand") {
      slotDisplayName = "in " + slotName;
    } else if (slotName === "accessory") {
      slotDisplayName = "as " + slotName;
    } else {
      slotDisplayName = "on " + slotName;
    }

    var slotData = {
      slotName: slotDisplayName,
      slotItemKey: null,
      slotItemName: null,
      slotItemIsMagic: false,
      slotItemQuality: "normal",
      slotItem: null
    };

    var slotItem = props.message.data.equipment[slotName];
    if (slotItem) {
      slotData.slotItemKey = slotItem.key;
      slotData.slotItemName = slotItem.name;
      slotData.slotItemIsMagic = slotItem.is_magic;
      slotData.slotItemQuality = slotItem.quality;
      slotData.slotItem = slotItem;
      slots.push(slotData);
    }
  }
  return slots;
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.equipment {
  table {
    display: table;
    border-collapse: collapse;
    border-spacing: 2px;
  }
  td.eq-item {
    padding: 2px 0px 2px 10px;
  }
}
</style>
