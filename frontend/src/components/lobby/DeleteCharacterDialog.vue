<template>
  <ModalView>
    <form class="delete-character-confirm" @submit.prevent="onSubmit">
      <h2>Delete {{ player.name }}?</h2>
      <p class="warning">This action cannot be undone.</p>

      <div class="form-group">
        <label for="delete-character-name" class="confirmation-label">
          Type <span class="character-name">{{ player.name }}</span> to confirm.
        </label>
        <input
          id="delete-character-name"
          ref="confirmationInput"
          v-model="confirmationName"
          autocomplete="off"
          :aria-invalid="confirmationName.length > 0 && !canDelete"
        />
      </div>

      <div v-if="confirmationName.length > 0 && !canDelete" class="error-message">
        Character name does not match.
      </div>
      <div v-if="errorMessage" class="error-message">{{ errorMessage }}</div>

      <div class="actions">
        <button type="button" class="btn-small button-gray" @click="onCancel">CANCEL</button>
        <button
          type="submit"
          class="btn-small button-red"
          :disabled="!canDelete || isDeleting"
        >
          {{ isDeleting ? "DELETING..." : "DELETE CHARACTER" }}
        </button>
      </div>
    </form>
  </ModalView>
</template>

<script lang="ts" setup>
import { computed, nextTick, onMounted, ref } from "vue";
import { useStore } from "vuex";
import ModalView from "@/components/ui/ModalView.vue";
import type { Player } from "@/core/interfaces";

const store = useStore();
const props = defineProps<{ player: Player }>();
const emit = defineEmits(["close"]);

const confirmationName = ref("");
const confirmationInput = ref<HTMLInputElement | null>(null);
const errorMessage = ref("");
const isDeleting = ref(false);

const normalizedPlayerName = computed(() => {
  return props.player.name.trim().toLowerCase();
});

const canDelete = computed(() => {
  return normalizedPlayerName.value.length > 0
    && confirmationName.value.trim().toLowerCase() === normalizedPlayerName.value;
});

onMounted(async () => {
  await nextTick();
  confirmationInput.value?.focus();
});

const onCancel = () => {
  emit("close");
};

const onSubmit = async () => {
  if (!canDelete.value || isDeleting.value) return;

  errorMessage.value = "";
  isDeleting.value = true;

  try {
    store.commit("lobby/char_id_set", props.player.id);
    await store.dispatch("lobby/char_delete");
    emit("close");
  } catch {
    errorMessage.value = "Unable to delete the character. Please try again.";
    isDeleting.value = false;
  }
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/fonts.scss";

.delete-character-confirm {
  padding: 10px 5px 0;

  h2 {
    @include font-title-regular;
    color: $color-secondary;
    font-size: 18px;
    letter-spacing: 1px;
    line-height: 22px;
    margin: 0 0 12px;
    overflow-wrap: anywhere;
  }

  .warning {
    @include font-text-light;
    color: $color-text-hex-70;
    line-height: 22px;
    margin: 0 0 18px;
  }

  .form-group {
    margin-bottom: 0.5rem;

    .confirmation-label {
      @include font-text-light;
      color: $color-text;
      letter-spacing: 0;
      line-height: 22px;
      white-space: normal;

      .character-name {
        color: $color-secondary;
        margin-left: 0;
        overflow-wrap: anywhere;
      }
    }
  }

  .error-message {
    @include font-text-light;
    color: $color-red;
    font-size: 13px;
    line-height: 18px;
    margin-top: 8px;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: flex-end;
    margin-top: 24px;

    button {
      min-height: 29px;

      &[disabled] {
        color: $color-text-hex-50;
        border-color: $color-text-hex-50;

        &:hover {
          background: transparent;
          color: $color-text-hex-50;
          cursor: default;
        }
      }
    }
  }
}
</style>
