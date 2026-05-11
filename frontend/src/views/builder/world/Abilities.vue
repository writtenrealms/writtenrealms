<template>
  <div id="world-abilities" v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <div class="section-header">
      <h1>{{ world.name.toUpperCase() }} ABILITIES</h1>
      <router-link :to="editWorldLink">Edit World</router-link>
    </div>

    <div v-if="isLoading" class="color-text-60">Loading abilities...</div>
    <div v-else-if="!abilities.length" class="color-text-60">No abilities configured.</div>

    <div v-else class="ability-list">
      <section
        v-for="ability in abilities"
        :key="ability.metadata?.slug || ability.metadata?.name"
        class="ability-entry"
      >
        <div class="ability-heading">
          <div>
            <h2>{{ ability.metadata?.name || ability.metadata?.slug }}</h2>
            <div class="color-text-60">{{ ability.metadata?.slug }}</div>
          </div>
          <span class="ability-status" :class="{ inactive: ability.spec?.is_active === false }">
            {{ ability.spec?.is_active === false ? "Inactive" : "Active" }}
          </span>
        </div>

        <div class="ability-summary">
          <div>
            <div class="summary-label">Commands</div>
            <ManifestValue :value="ability.spec?.command?.verbs || []" />
          </div>
          <div>
            <div class="summary-label">Action Type</div>
            <ManifestValue :value="ability.spec?.action_type" />
          </div>
          <div>
            <div class="summary-label">Target</div>
            <ManifestValue :value="ability.spec?.target || {}" />
          </div>
        </div>

        <div class="ability-spec">
          <div
            v-for="entry in abilitySpecEntries(ability)"
            :key="entry.key"
            class="ability-spec-row"
          >
            <div class="spec-label">{{ entry.label }}</div>
            <ManifestValue :value="entry.value" />
          </div>
        </div>
      </section>
    </div>
  </div>
  <div v-else>
    You do not have permission to view abilities for this world.
  </div>
</template>

<script lang="ts" setup>
import axios from "axios";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import ManifestValue from "@/components/builder/world/ManifestValue.vue";

const store = useStore();
const route = useRoute();

const exportPayload = ref<any | null>(null);
const isLoading = ref(false);
const world = computed(() => store.state.builder.world);
const exportEndpoint = computed(() => `/builder/worlds/${route.params.world_id}/export/`);

const abilities = computed(() => {
  const documents = exportPayload.value?.documents || [];
  return documents.filter((document) => String(document.kind || "").toLowerCase() === "ability");
});

const editWorldLink = computed(() => ({
  name: "builder_world_edit",
  params: { world_id: route.params.world_id },
}));

const labelForKey = (key: string) => {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const abilitySpecEntries = (ability) => {
  const hiddenSummaryKeys = new Set(["command", "action_type", "target", "is_active"]);
  return Object.entries(ability.spec || {})
    .filter(([key]) => !hiddenSummaryKeys.has(key))
    .map(([key, value]) => ({
      key,
      label: labelForKey(key),
      value,
    }));
};

onMounted(async () => {
  isLoading.value = true;
  try {
    const resp = await axios.get(exportEndpoint.value);
    exportPayload.value = resp.data;
  } catch {
    store.commit("ui/notification_set_error", "Unable to load abilities.");
  } finally {
    isLoading.value = false;
  }
});
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.section-header {
  align-items: baseline;
  display: flex;
  gap: 1rem;
  justify-content: space-between;
  margin-bottom: 1.5rem;

  @media ($mobile-site) {
    align-items: flex-start;
    flex-direction: column;
  }
}

.ability-list {
  display: grid;
  gap: 1.5rem;
  max-width: 960px;
}

.ability-entry {
  border-top: 1px solid $color-background-light-border;
  padding-top: 1.25rem;
}

.ability-heading {
  display: flex;
  gap: 1rem;
  justify-content: space-between;
  margin-bottom: 1rem;

  h2 {
    margin-bottom: 0.25rem;
  }
}

.ability-status {
  border: 1px solid $color-green;
  color: $color-green;
  height: fit-content;
  padding: 0.2rem 0.5rem;

  &.inactive {
    border-color: $color-text-hex-50;
    color: $color-text-hex-50;
  }
}

.ability-summary {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  margin-bottom: 1rem;
}

.summary-label,
.spec-label {
  color: $color-text-hex-60;
  margin-bottom: 0.35rem;
}

.ability-spec {
  display: grid;
  gap: 1rem;
}

.ability-spec-row {
  border-left: 1px solid $color-background-light-border;
  padding-left: 1rem;
}
</style>
