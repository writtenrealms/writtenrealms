<template>
  <div v-if="parts">
    <template v-for="(part, index) in parts" :key="index">
      <strong v-if="part.emphasized" class="color-primary">{{ part.text }}</strong>
      <template v-else>{{ part.text }}</template>
    </template>
  </div>
  <Message v-else :message="message" />
</template>

<script lang="ts" setup>
import { computed } from "vue";
import Message from "@/components/game/console/Message.vue";

const props = defineProps<{ message: any }>();

const parts = computed(() => {
  const isInterrupt = props.message.type === "notification.combat.ability_interrupted";
  const name = isInterrupt
    ? props.message.data?.interrupted_ability?.name
    : props.message.data?.ability?.name;
  const text = props.message.text;
  if (typeof name !== "string" || !name || typeof text !== "string") return null;

  // Match the final ability label, even when the mob's name contains it too.
  const suffix = `${name}.`;
  if (!text.endsWith(suffix)) return null;
  const prefix = text.slice(0, -suffix.length);

  // Anchor the action to its sentence position so names containing these
  // words stay plain. Both interrupt recipient perspectives share this view.
  const action = isInterrupt
    ? prefix.match(/^(.* )(interrupts)( your (?:cast|channel) of )$/)
      || prefix.match(/^(You )(interrupt)( .*)$/)
    : prefix.match(/^(.* )(charges|continues charging)( )$/);
  const prefixParts = action
    ? [
      { text: action[1], emphasized: false },
      { text: action[2], emphasized: true },
      { text: action[3], emphasized: false },
    ]
    : [{ text: prefix, emphasized: false }];

  return [
    ...prefixParts,
    { text: name, emphasized: true },
    { text: ".", emphasized: false },
  ];
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

strong {
  font-weight: bold;
}
</style>
