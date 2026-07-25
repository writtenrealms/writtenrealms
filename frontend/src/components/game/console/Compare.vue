<template>
  <div>
    <div class="mb-2">Click on a slot to view the options for it:</div>

    <div class="flex">
      <div class='slots'>
        <div v-for="(slot, index) in slots"
             :key="index" @click="onClickSlot(slot)"
             class='hover'
             :class="{'color-text-50': slot != selectedSlot}">
          {{ slot }}
        </div>
      </div>
      <div class="slot-options ml-4 grow">
        <div v-if="selectedSlot">
          <div v-if="message.data[selectedSlot] && message.data[selectedSlot].length">
            <div v-for="item in message.data[selectedSlot]" :key="item.key">
              <span v-if="isEquipped(item, selectedSlot)">*</span>
              <span
                v-if="isLastMessage && isCurrentItem(item)"
                v-interactive="{
                  target: item,
                  primaryAction: true,
                  actionContext: actionContextFor(item),
                }"
                class='interactive'
                :class="[item.quality]"
                :key="item.key + '-interactive'"
              >{{ item.name }}</span>
              <span v-else
                :class="[item.quality]"
                :key="item.key"
              >{{ item.name }}</span>
            </div>
          </div>
          <div v-else>
            No items for this slot.
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang='ts' setup>
import { computed, ref } from "vue";
import { useStore } from "vuex";
import { EQUIPMENT_SLOT_LIST } from "@/constants.ts";
import { getItemActionContextSnapshot } from "@/core/itemActions";

const store = useStore();
const props = defineProps<{ message: any }>();

const selectedSlot = ref("weapon");

const originContextByKey = getItemActionContextSnapshot(
  props.message,
  store.state.game,
);
const actionContextFor = (item: any) => originContextByKey.get(item.key);

const currentInventoryKeys = computed(() => new Set(
  (store.state.game.player?.inventory || []).map((item: any) => item.key),
));
const currentEquipmentKeys = computed(() => new Set(
  Object.values(store.state.game.player?.equipment || {})
    .filter(Boolean)
    .map((item: any) => item.key),
));
const isCurrentItem = (item: any) => {
  const context = actionContextFor(item);
  if (context === "inventory") {
    return currentInventoryKeys.value.has(item.key);
  }
  if (context === "equipment") {
    return currentEquipmentKeys.value.has(item.key);
  }
  return false;
};

const isLastMessage = computed(() => {
  return store.state.game.last_message[props.message.type] == props.message;
});
const slots = EQUIPMENT_SLOT_LIST;

const onClickSlot = (slot) => {
  selectedSlot.value = slot;
};

const isEquipped = (item, slot) => {
  if (
    store.state.game.player.equipment[slot] &&
    item.key == store.state.game.player.equipment[slot].key
  )
    return true;
  return false;
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
