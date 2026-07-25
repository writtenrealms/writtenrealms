<template>
  <div>
    <div class="equipment-view">
      <table>
        <tr v-for="slotData in slots" :key="slotData.slotName">
          <td class="eq_slot">{{ slotData.slotName }}</td>
          <td class="eq-item">
            <span
              v-interactive="{
                target: slotData.slotItem,
                primaryAction: true,
                actionContext: 'equipment',
              }"
              class="interactive"
              :class="[slotData.slotItemQuality, slotData.slotItemKey ? 'interactive' : '']"
              :key="slotData.slotItemKey"
              :keyword="slotData.slotItemKeyword"
            >{{ slotData.slotItemName }}</span>
          </td>
        </tr>
      </table>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from 'vuex';
import { EQUIPMENT_SLOT_LIST } from "@/constants";

const store = useStore();

const playerEq = computed(() => store.state.game.player.equipment);

const slots: any = computed(() => {
  let slots: {}[] = [];
  for (const slotName of EQUIPMENT_SLOT_LIST) {
    const slotData = {
      slotName: slotName,
      slotItemKey: "",
      slotItemName: "<empty>",
      slotItemQuality: "",
      slotItemKeyword: "",
      slotItem: null
    };

    const slotItem = playerEq.value[slotName];
    if (slotItem) {
      slotData.slotItemKey = slotItem.key;
      slotData.slotItemName = slotItem.name;
      slotData.slotItemQuality = slotItem.quality;
      slotData.slotItemKeyword = slotItem.keywords.split(" ")[0];
      slotData.slotItem = slotItem;
    }

    slots.push(slotData);
  }
  return slots;
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
.equipment-view {
  padding: 0 20px;

  display: flex;
  flex-wrap: wrap;
  .equipment-slot {
    flex: 1 1 50%;
  }

  .eq-slot {
    vertical-align: top;
  }
  .eq-item {
    padding-left: 1em;
  }
}
</style>
