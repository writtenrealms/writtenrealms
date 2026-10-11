<template>
  <VMenu ref="menu" :triggers="mobile ? ['click'] : ['hover', 'focus']" :show-triggers="mobile ? ['click'] : showTriggers"
    :hide-triggers="mobile ? [] : ['hover', 'focus']"
    :popper-triggers="[]" :delay="{ show: 0, hide: 150 }"
    :placement="mobile ? 'bottom' : 'right-start'" :shift-cross-axis="true"
    :distance="12" :overflow-padding="12" :no-auto-focus="true">
    <template #default="{ shown, hide }">
      <button class="mob-preview-name" type="button" aria-haspopup="dialog"
        :aria-expanded="shown" @keydown.esc.stop="hide({ skipDelay: true })">{{ mob.name }}</button>
    </template>
    <template #popper="{ hide }">
      <section class="mob-definition-preview" role="dialog" :aria-label="`${mob.name} details`"
        @pointerenter="keepOpen" @pointerleave="leave" @focusin="keepOpen" @focusout="leave">
        <button class="icon-button mob-preview-close" aria-label="Close mob details" @click="hide({ skipDelay: true })">
          <X aria-hidden="true" />
        </button>
        <h3>{{ mob.name }}</h3>
        <div class="color-text-60">{{ mob.type }} · {{ mob.slug }}</div>
        <p v-if="mob.room_description" class="color-text-70">{{ mob.room_description }}</p>
        <p v-if="mob.description" class="mob-description">{{ mob.description }}</p>
        <template v-for="(value, label) in detailSections" :key="label">
          <template v-if="value && Object.keys(value).length">
            <h4>{{ label }}</h4>
            <ManifestValue :value="value" :collapse-complex="true" />
          </template>
        </template>
        <router-link class="btn-thin" :to="{
          name: 'builder_mob_definition_details', params: { world_id: worldId, mob_definition_id: mob.id },
        }" @click="hide()">VIEW MOB YAML</router-link>
      </section>
    </template>
  </VMenu>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { Menu as VMenu } from 'floating-vue';
import { X } from '@lucide/vue';
import ManifestValue from '@/components/builder/world/ManifestValue.vue';
import type { MobDefinitionPreviewData } from '@/core/spawnPlanPreview';

const props = defineProps<{ mob: MobDefinitionPreviewData; worldId: string | number }>();
const menu = ref<InstanceType<typeof VMenu> | null>(null);
const mobile = ref(false);
// Manage the popup's hover directly so explicit dismissal is never delayed by
// the menu library's pointer-aiming heuristic.
const keepOpen = () => { if (!mobile.value) menu.value?.show(); };
const leave = (event: Event) => {
  if (!mobile.value && !(event.currentTarget as HTMLElement).contains(document.activeElement)) menu.value?.hide();
};
let media: MediaQueryList;
const updateMobile = () => { mobile.value = media.matches; };
onMounted(() => {
  media = window.matchMedia('(max-width: 768px), (hover: none)');
  updateMobile();
  media.addEventListener('change', updateMobile);
});
onBeforeUnmount(() => media?.removeEventListener('change', updateMobile));
// Tap opens the preview without a second click toggling it closed after focus.
const showTriggers = ['hover', 'focus', 'click'] as ('hover' | 'focus' | 'click')[];
const detailSections = computed(() => ({
  Properties: props.mob.base_properties,
  Attributes: props.mob.attributes,
  Traits: props.mob.traits,
  Randomization: props.mob.randomization,
}));
</script>

<style scoped lang="scss">
@import '@/styles/colors.scss';

.mob-preview-name {
  background: none;
  border: 0;
  padding: 0.25rem 0;
  color: $color-primary;
  cursor: pointer;
  font: inherit;
  text-align: left;
  text-decoration: underline dotted;
  text-underline-offset: 0.2em;
}
.mob-definition-preview {
  width: 280px;
  max-width: calc(100vw - 50px);
  max-height: min(560px, calc(100dvh - 70px));
  overflow: auto;
  overscroll-behavior: contain;
  overflow-wrap: anywhere;
  font-size: 0.9rem;

  h3 { margin: 0 1.75rem 0.35rem 0; }
  h4 { color: $color-text-hex-70; margin: 0.8rem 0 0.35rem; }
  p { margin: 0.75rem 0; }
  .mob-description { white-space: pre-line; }
  .mob-preview-close { float: right; }
  .btn-thin { display: inline-block; margin-top: 0.75rem; }
  :deep(.manifest-key-value-table th) { width: 55%; }
}
</style>
