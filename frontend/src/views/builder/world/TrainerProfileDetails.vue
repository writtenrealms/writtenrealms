<template>
  <ManifestResourceDetails
    :world-id="worldId"
    :resource-id="profileId"
    resource-label="trainer profile"
    resource-title="Trainer profile"
    list-label="Trainer Profiles"
    expected-kind="trainerprofile"
    response-field="trainer_profile"
    list-route-name="builder_trainer_profile_list"
    detail-route-name="builder_trainer_profile_details"
    detail-id-param="trainer_profile_id"
    :load-resource="loadResource"
    :inherited-world="inheritedWorld"
  >
    <template #header="{ resource: profile }">
      <h2 class="definition-title">{{ profile.name || profile.slug }}</h2>
      <div class="definition-meta color-text-60">
        ID: {{ profile.id }} | Slug: {{ profile.slug }}
      </div>
    </template>

    <template #summary="{ resource: profile }">
      <p v-if="profile.notes" class="profile-notes">
        <span class="summary-label">Notes</span>
        {{ profile.notes }}
      </p>

      <section class="learning-policy">
        <span class="summary-label">Learning policy</span>
        <div>{{ learningLimitSummary(profile) }}</div>
        <div v-if="!hasLearningConditions(profile)" class="color-text-60">
          Available to every otherwise eligible learner.
        </div>
        <details v-else class="learning-conditions">
          <summary>Eligibility conditions</summary>
          <pre>{{ formattedLearningConditions(profile) }}</pre>
        </details>
      </section>

      <details class="ability-relationships">
        <summary>{{ abilitySummary(profile) }}</summary>
        <div v-if="abilities(profile).length" class="ability-references">
          <span
            v-for="ability in abilities(profile)"
            :key="ability.key || ability.id"
            class="ability-chip"
          >
            <strong>{{ ability.name }}</strong>
            <span class="color-text-60">{{ ability.slug }}</span>
          </span>
        </div>
        <div v-else class="color-text-60">No abilities assigned.</div>
      </details>
    </template>
  </ManifestResourceDetails>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import ManifestResourceDetails from "@/components/builder/world/ManifestResourceDetails.vue";
import {
  fetchTrainerProfile,
  type TrainerProfileAbility,
  type TrainerProfileLearningPolicy,
} from "@/services/trainers";

const route = useRoute();
const store = useStore();
const worldId = computed(() => String(route.params.world_id));
const profileId = computed(() => String(route.params.trainer_profile_id));
const inheritedWorld = computed(() => store.state.builder.world.instance_of || {});
const loadResource = () => fetchTrainerProfile(worldId.value, profileId.value);

const abilities = (profile: any): TrainerProfileAbility[] => (
  Array.isArray(profile?.abilities) ? profile.abilities : []
);

const abilitySummary = (profile: any): string => {
  const count = abilities(profile).length || Number(profile?.ability_count || 0);
  return `${count} ${count === 1 ? "ability" : "abilities"}`;
};

const learningPolicy = (profile: any): TrainerProfileLearningPolicy => (
  profile?.learning && typeof profile.learning === "object"
    ? profile.learning
    : {}
);

const learningConditions = (profile: any): unknown => (
  learningPolicy(profile).conditions ?? {}
);

const hasLearningConditions = (profile: any): boolean => {
  const conditions = learningConditions(profile);
  if (Array.isArray(conditions)) return conditions.length > 0;
  if (conditions && typeof conditions === "object") {
    return Object.keys(conditions as Record<string, unknown>).length > 0;
  }
  return typeof conditions === "boolean";
};

const learningLimitSummary = (profile: any): string => {
  const maxKnown = learningPolicy(profile).max_known;
  if (maxKnown === null || maxKnown === undefined || maxKnown === "uncapped") {
    return "No profile-specific selection limit.";
  }
  const limit = Number(maxKnown);
  return Number.isFinite(limit)
    ? `Each learner may select up to ${limit} ${limit === 1 ? "ability" : "abilities"} from this profile.`
    : "No profile-specific selection limit.";
};

const formattedLearningConditions = (profile: any): string => (
  JSON.stringify(learningConditions(profile), null, 2)
);
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.definition-title {
  margin-bottom: 0.35rem;
}

.definition-meta {
  min-width: 0;
  overflow-wrap: anywhere;
}

.profile-notes,
.learning-policy,
.ability-relationships {
  margin-top: 0.75rem;
}

.summary-label {
  color: $color-text-hex-60;
  display: block;
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.ability-relationships summary {
  cursor: pointer;
}

.learning-conditions {
  margin-top: 0.35rem;
}

.learning-conditions summary {
  cursor: pointer;
}

.learning-conditions pre {
  background: rgba(0, 0, 0, 0.18);
  margin: 0.4rem 0 0;
  max-width: 100%;
  overflow-x: auto;
  padding: 0.5rem;
  white-space: pre-wrap;
}

.ability-references {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.5rem;
}

.ability-chip {
  border: 1px solid $color-form-border;
  display: inline-flex;
  flex-direction: column;
  gap: 0.1rem;
  padding: 0.25rem 0.45rem;
}
</style>
