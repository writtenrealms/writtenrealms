<template>
  <div class="expanded-actions" ref="outsideClickRef">
    <div class="expanded-action hover" @click="onClickEditChar">edit</div>
    <div class="expanded-action hover" @click="onClickDeleteChar">delete</div>
  </div>
</template>

<script lang="ts" setup>
import { computed, ComputedRef } from "vue";
import { useStore } from "vuex";
import { FormElement } from "@/core/forms";
import { World, Player } from "@/core/interfaces";
import { onOutsideClick } from "@/composables/onOutsideClick";
import DeleteCharacterDialog from "./DeleteCharacterDialog.vue";

const store = useStore();
const world: ComputedRef<World> = computed(() => store.state.lobby.world);
const props = defineProps<{ player: Player }>();
const emit = defineEmits(['close']);

// Compose outside click ref
const outsideClickRef = onOutsideClick(() => {
  emit('close');
});

const onClickEditChar = () => {
  const description: FormElement = {
    attr: "description",
    label: "Description",
    widget: "textarea",
  }

  const schema: FormElement[] = [description];

  const modal = {
    data: {
      description: props.player.description,
      player_id: props.player.id,
      world_id: world.value.id,
    },
    title: `EDIT PLAYER`,
    schema: schema,
    action: "lobby/char_edit"
  };
  store.commit('lobby/char_id_set', props.player.id);
  store.commit("ui/modal/open_form", modal);
  emit('close');
}

const onClickDeleteChar = () => {
  store.commit("ui/modal/open_view", {
    component: DeleteCharacterDialog,
    props: {
      player: props.player,
    },
  });
  emit('close');
}
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.expanded-action {
  padding: 0px 5px;
  padding-left: 8px;
  border-top: 1px solid #333;
  width: 75px;

  &:hover {
    color: $color-primary;
  }
}
</style>
