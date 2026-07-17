<template>
  <div class="salvage-list indented">
    <div v-if="items.length === 0">You have nothing you can salvage.</div>
    <template v-else>
      <div>You can salvage:</div>
      <ol class="list mt-4">
        <li
          v-for="item in items"
          :key="item.key"
          :value="item.number"
          class="inventory-item"
        >
          <span
            v-if="isLastMessage"
            v-interactive="{ target: item }"
            class="interactive"
            :class="[item.quality]"
          >{{ item.name }}</span>
          <span v-else :class="[item.quality]">{{ item.name }}</span>
        </li>
      </ol>
      <div v-if="message.data.truncated" class="color-text-60 mt-4">
        Only the first {{ message.data.limit }} items are shown.
      </div>
      <div class="color-text-60 mt-4">Use: salvage &lt;number&gt;</div>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useStore } from "vuex";

const store = useStore();
const props = defineProps<{ message: any }>();

const items = computed(() => props.message.data.items || []);
const isLastMessage = computed(
  () => store.state.game.last_message[props.message.type] == props.message,
);
</script>
