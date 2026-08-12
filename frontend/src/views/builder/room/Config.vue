<template>
  <div id="room_flags" v-if="loaded">
    <h2>ROOM CONFIG</h2>

    <h3 class='mb-4'>ROOM FLAGS</h3>

    <div v-for="flag in flags" :key="flag.code">
      <label>
        <input type="checkbox" :checked="flag.value" @input="onChangeFlag(flag.code)" />
        {{ flag.label }}
        <Help :help="flagHelp[flag.code]" v-if="flagHelp[flag.code]"/>
      </label>
    </div>

    <section class="room-services mt-8">
      <h3 class="mb-4">ROOM SERVICES</h3>

      <article class="service-card">
        <div class="service-heading">
          <div>
            <h4>SHOP</h4>
            <p class="color-text-60">
              Attach a Merchant Profile to make its shop available directly in this room.
            </p>
          </div>
          <span class="service-availability">Always available</span>
        </div>

        <div v-if="canEdit" class="form-group merchant-profile-field">
          <label for="field-merchant_profile">Merchant Profile</label>
          <ReferenceField
            :schema="merchantProfileSchema"
            :model-value="merchantProfile"
            :endpoint="merchantProfileEndpoint"
            @update="onUpdateMerchantProfile"
          />
        </div>
        <div v-else class="selected-profile">
          <span class="selected-profile-label">Merchant Profile</span>
          <span v-if="merchantProfile">{{ merchantProfile.name }} ({{ merchantProfile.slug }})</span>
          <span v-else class="color-text-60">None attached</span>
        </div>

        <p v-if="profileIsInherited" class="color-text-60 inherited-profile-note">
          Inherited from {{ merchantProfileWorld?.name }}.
        </p>

        <div class="service-actions">
          <router-link
            v-if="merchantProfileLink"
            class="profile-link"
            :to="merchantProfileLink"
          >OPEN PROFILE</router-link>
          <button
            v-if="canEdit"
            class="btn-thin"
            :disabled="!merchantProfile || isSavingMerchant"
            @click="onClearMerchantProfile"
          >CLEAR</button>
          <button
            v-if="canEdit"
            class="btn-medium"
            :disabled="!merchantProfileDirty || isSavingMerchant"
            @click="onSaveMerchantProfile"
          >{{ isSavingMerchant ? "SAVING..." : "SAVE" }}</button>
        </div>

        <p v-if="!canEdit" class="color-text-60 service-readonly-note">
          This room is not assigned to you, so its attached services are read-only.
        </p>
      </article>

      <article class="service-card mt-4">
        <div class="service-heading">
          <div>
            <h4>ABILITY TRAINING</h4>
            <p class="color-text-60">
              Attach a Trainer Profile to make its abilities learnable directly in this room.
            </p>
          </div>
          <span class="service-availability">Always available</span>
        </div>

        <div v-if="canEditTraining" class="form-group trainer-profile-field">
          <label for="field-trainer_profile">Trainer Profile</label>
          <ReferenceField
            :schema="trainerProfileSchema"
            :model-value="trainerProfile"
            :endpoint="trainerProfileEndpoint"
            @update="onUpdateTrainerProfile"
          />
        </div>
        <div v-else class="selected-profile">
          <span class="selected-profile-label">Trainer Profile</span>
          <span v-if="trainerProfile">{{ trainerProfile.name }} ({{ trainerProfile.slug }})</span>
          <span v-else class="color-text-60">None attached</span>
        </div>

        <p v-if="trainerProfileIsInherited" class="color-text-60 inherited-profile-note">
          Inherited from {{ trainerProfileWorld?.name }}.
        </p>

        <div class="service-actions">
          <router-link
            v-if="trainerProfileLink"
            class="profile-link"
            :to="trainerProfileLink"
          >OPEN PROFILE</router-link>
          <button
            v-if="canEditTraining"
            class="btn-thin"
            :disabled="!trainerProfile || isSavingTrainer"
            @click="onClearTrainerProfile"
          >CLEAR</button>
          <button
            v-if="canEditTraining"
            class="btn-medium"
            :disabled="!trainerProfileDirty || isSavingTrainer"
            @click="onSaveTrainerProfile"
          >{{ isSavingTrainer ? "SAVING..." : "SAVE" }}</button>
        </div>

        <p v-if="!canEditTraining" class="color-text-60 service-readonly-note">
          Trainer attachments affect world-wide ability availability and require a
          senior builder. This room's training service is read-only for you.
        </p>
      </article>

      <p class="service-note color-text-60">
        A room shop does not require a mob. For a shop that should close when an NPC
        leaves or dies, leave this blank, attach the profile to a Mob Definition, and
        place that mob through
        <router-link :to="roomSpawnsLink">Spawns</router-link>.
      </p>
      <p class="service-note color-text-60">
        Room training does not require a mob. For training that should only be
        available while an NPC is present, leave this blank, attach the profile to
        a Mob Definition, and place that mob through
        <router-link :to="roomSpawnsLink">Spawns</router-link>.
      </p>
    </section>

    <div v-if="store.state.builder.world.builder_info.builder_rank > 2 && has_instances">
      <h3 class="mt-8 mb-4">INSTANCE LINK</h3>

      <p>If a room is linked to an instance, a player will be able to enter it via the 'enter' command.</p>

      <div class="form-group transfer_to">
        <ReferenceField
          :schema="transfer_to_schema"
          v-model="transfer_to"
          :endpoint="transfer_to_endpoint"
          @update="onUpdateTransferTo"/>
      </div>

      <div v-if="transfer_to && transfer_to_world" class="mb-4">
        Links to:
        <a :href="instanceRoomLink(transfer_to_world.id, transfer_to)">
          {{ transfer_to.name }}
        </a>
      </div>

      <button class="btn-medium" @click="onSaveInstanceLink">SAVE</button>
    </div>

  </div>
</template>

<script lang='ts' setup>
import { computed, ref, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";
import axios from "axios";
import Help from "@/components/Help.vue";
import ReferenceField from "@/components/forms/ReferenceField.vue";
import { builderRoomIndexRoute } from "@/core/builderRoutes";
import { manifestApiErrorMessage } from "@/services/manifests";
import { merchantProfileListEndpoint } from "@/services/merchants";
import { trainerProfileListEndpoint } from "@/services/trainers";
import {
  fetchRoomConfig,
  updateRoomMerchantProfile,
  updateRoomTrainerProfile,
  type BuilderReference,
  type RoomConfigPayload,
} from "@/services/roomConfig";

const route = useRoute();
const router = useRouter();
const store = useStore();

interface RoomFlag {
  code: string;
  label: string;
  value: boolean;
}

const flags = ref<RoomFlag[]>([]);
const transfer_to = ref<BuilderReference | null>(null);
const transfer_to_world = ref<any>(null);
const merchantProfile = ref<BuilderReference | null>(null);
const merchantProfileWorld = ref<BuilderReference | null>(null);
const savedMerchantProfileId = ref<number | null>(null);
const trainerProfile = ref<BuilderReference | null>(null);
const trainerProfileWorld = ref<BuilderReference | null>(null);
const savedTrainerProfileId = ref<number | null>(null);
const configCanEdit = ref<boolean | null>(null);
const configCanEditTraining = ref<boolean | null>(null);
const isSavingMerchant = ref(false);
const isSavingTrainer = ref(false);
const has_instances = ref(false);
const loaded = ref(false);

const worldId = computed(() => String(route.params.world_id));
const roomId = computed(() => store.state.builder.room.id);
const canEdit = computed(() => (
  configCanEdit.value ?? store.state.builder.room?.has_assignment === true
));
const canEditTraining = computed(() => (
  configCanEditTraining.value
  ?? Number(store.state.builder.world?.builder_info?.builder_rank || 0) > 2
));
const merchantProfileEndpoint = computed(() => merchantProfileListEndpoint(worldId.value));
const trainerProfileEndpoint = computed(() => trainerProfileListEndpoint(worldId.value));
const profileIsInherited = computed(() => Boolean(
  merchantProfileWorld.value?.id
  && String(merchantProfileWorld.value.id) !== worldId.value
));
const merchantProfileLink = computed(() => {
  if (!merchantProfile.value?.id) return null;
  return {
    name: "builder_merchant_profile_details",
    params: {
      world_id: route.params.world_id,
      merchant_profile_id: merchantProfile.value.id,
    },
  };
});
const merchantProfileDirty = computed(() => (
  (merchantProfile.value?.id ?? null) !== savedMerchantProfileId.value
));
const trainerProfileIsInherited = computed(() => Boolean(
  trainerProfileWorld.value?.id
  && String(trainerProfileWorld.value.id) !== worldId.value
));
const trainerProfileLink = computed(() => {
  if (!trainerProfile.value?.id) return null;
  return {
    name: "builder_trainer_profile_details",
    params: {
      world_id: route.params.world_id,
      trainer_profile_id: trainerProfile.value.id,
    },
  };
});
const trainerProfileDirty = computed(() => (
  (trainerProfile.value?.id ?? null) !== savedTrainerProfileId.value
));
const roomSpawnsLink = computed(() => ({
  name: "builder_room_spawn_plan_list",
  params: {
    world_id: route.params.world_id,
    room_relative_id: store.state.builder.room.relative_id,
  },
}));

const applyRoomConfig = (config: RoomConfigPayload) => {
  if ("transfer_to" in config) transfer_to.value = config.transfer_to || null;
  if ("transfer_to_world" in config) {
    transfer_to_world.value = config.transfer_to_world || null;
  }
  if ("has_instances" in config) has_instances.value = Boolean(config.has_instances);
  if ("merchant_profile" in config) {
    merchantProfile.value = config.merchant_profile || null;
    savedMerchantProfileId.value = config.merchant_profile?.id ?? null;
  }
  if ("merchant_profile_world" in config) {
    merchantProfileWorld.value = config.merchant_profile_world || null;
  }
  if ("trainer_profile" in config) {
    trainerProfile.value = config.trainer_profile || null;
    savedTrainerProfileId.value = config.trainer_profile?.id ?? null;
  }
  if ("trainer_profile_world" in config) {
    trainerProfileWorld.value = config.trainer_profile_world || null;
  }
  if (typeof config.can_edit === "boolean") {
    configCanEdit.value = config.can_edit;
  }
  if (typeof config.can_edit_training === "boolean") {
    configCanEditTraining.value = config.can_edit_training;
  }
};

onMounted(async () => {
  try {
    const [flagsResponse, config] = await Promise.all([
      axios.get(`/builder/worlds/${worldId.value}/rooms/${roomId.value}/flags/`),
      fetchRoomConfig(worldId.value, roomId.value),
    ]);
    flags.value = flagsResponse.data;
    applyRoomConfig(config);
  } catch (error: unknown) {
    store.commit(
      "ui/notification_set_error",
      manifestApiErrorMessage(error, "Could not load room config."),
    );
  } finally {
    loaded.value = true;
  }
});

const onChangeFlag = async (code: string) => {
  const world_id = route.params.world_id;
  const room_id = store.state.builder.room.id;
  for (let flag of flags.value) {
    if (flag.code == code) {
      const resp = await axios.post(`/builder/worlds/${world_id}/rooms/${room_id}/flags/${code}/`);
      flag = resp.data;
    }
  }
};

const flagHelp = {
  no_roam: "Rooms flagged as No Roam are excluded from the rooms that a wandering mob might load or move into.",
  peaceful: "Characters cannot engage in combat in peaceful rooms.",
  no_quit: "Players cannot quit in No Quit rooms.",
  landmark: "A landmark room is shown on the player's map even if they've never visited it.",
};

const merchantProfileSchema = {
  attr: "merchant_profile",
  label: "Merchant Profile",
  references: "merchantprofile",
  widget: "reference",
};

const onUpdateMerchantProfile = (value: BuilderReference | null) => {
  merchantProfile.value = value;
};

const onClearMerchantProfile = () => {
  merchantProfile.value = null;
};

const onSaveMerchantProfile = async () => {
  if (!canEdit.value || !merchantProfileDirty.value) return;
  isSavingMerchant.value = true;
  try {
    const profileId = merchantProfile.value?.id ?? null;
    const config = await updateRoomMerchantProfile(
      worldId.value,
      roomId.value,
      profileId,
    );
    applyRoomConfig(config);
    store.commit("ui/notification_set", "Room shop saved.");
  } catch (error: unknown) {
    store.commit(
      "ui/notification_set_error",
      manifestApiErrorMessage(error, "Could not save the room shop."),
    );
  } finally {
    isSavingMerchant.value = false;
  }
};

const trainerProfileSchema = {
  attr: "trainer_profile",
  label: "Trainer Profile",
  references: "trainerprofile",
  widget: "reference",
};

const onUpdateTrainerProfile = (value: BuilderReference | null) => {
  trainerProfile.value = value;
};

const onClearTrainerProfile = () => {
  trainerProfile.value = null;
};

const onSaveTrainerProfile = async () => {
  if (!canEditTraining.value || !trainerProfileDirty.value) return;
  isSavingTrainer.value = true;
  try {
    const profileId = trainerProfile.value?.id ?? null;
    const config = await updateRoomTrainerProfile(
      worldId.value,
      roomId.value,
      profileId,
    );
    applyRoomConfig(config);
    store.commit("ui/notification_set", "Room training saved.");
  } catch (error: unknown) {
    store.commit(
      "ui/notification_set_error",
      manifestApiErrorMessage(error, "Could not save the room training profile."),
    );
  } finally {
    isSavingTrainer.value = false;
  }
};

const transfer_to_schema = {
  attr: "room",
  label: "Instance Link",
  references: "room",
  widget: "reference",
}

const onUpdateTransferTo = (value) => {
  transfer_to.value = value;
};

const transfer_to_endpoint = `builder/worlds/${store.state.builder.world.id}/instancerooms/`;

const onSaveInstanceLink = async () => {
  const resp = await axios.patch(`/builder/worlds/${route.params.world_id}/rooms/${store.state.builder.room.id}/config/`, {
    transfer_to: transfer_to.value ? transfer_to.value.id : null
  });
  if (resp.status == 200) {
    transfer_to.value = resp.data.transfer_to;
    transfer_to_world.value = resp.data.transfer_to_world;
    store.commit('ui/notification_set', 'Instance link saved.');
  }
};

const instanceRoomLink = (instance_id, room) => {
  return router.resolve(builderRoomIndexRoute(instance_id, room)).href;
};
</script>

<style lang='scss' scoped>
h2 {
  margin-bottom: 20px;
}

div.form-group.transfer_to > .reference-field > .reference-input > div > input,
div.form-group.transfer_to > .reference-field > .reference-input > input {
  width: auto !important;
}

.room-services {
  max-width: 52rem;
}

.service-card {
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid rgba(255, 255, 255, 0.15);
  box-sizing: border-box;
  padding: 1rem;
  width: 100%;
}

.service-heading {
  align-items: flex-start;
  display: flex;
  gap: 1rem;
  justify-content: space-between;

  h4,
  p {
    margin: 0;
  }

  p {
    line-height: 1.45;
    margin-top: 0.35rem;
  }
}

.service-availability {
  border: 1px solid rgba(255, 255, 255, 0.2);
  flex: 0 0 auto;
  font-size: 0.75rem;
  padding: 0.25rem 0.45rem;
  text-transform: uppercase;
}

.merchant-profile-field,
.trainer-profile-field,
.selected-profile {
  margin-top: 1rem;
  max-width: 32rem;
}

.merchant-profile-field > label,
.trainer-profile-field > label,
.selected-profile-label {
  display: block;
  font-size: 0.8rem;
  margin-bottom: 0.35rem;
  text-transform: uppercase;
}

.merchant-profile-field :deep(.reference-field),
.merchant-profile-field :deep(input),
.trainer-profile-field :deep(.reference-field),
.trainer-profile-field :deep(input) {
  box-sizing: border-box;
  width: 100%;
}

.service-actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin-top: 1rem;
}

.profile-link {
  font-size: 0.8rem;
  margin-right: auto;
}

.service-readonly-note,
.inherited-profile-note,
.service-note {
  line-height: 1.45;
  margin-bottom: 0;
  margin-top: 0.8rem;
}

@media (max-width: 600px) {
  .service-heading {
    flex-direction: column;
  }

  .service-actions {
    align-items: stretch;
    flex-direction: column;

    button,
    .profile-link {
      box-sizing: border-box;
      margin-right: 0;
      text-align: center;
      width: 100%;
    }
  }
}
</style>
