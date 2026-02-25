<template>
  <div id="world-config" class="builder-config" v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <h2>{{ world.name.toUpperCase() }} CONFIG</h2>

    <div class="general-settings mt-6">
      <!-- <h3>GENERAL SETTINGS</h3> -->

      <div class="color-text-60">
        <span v-if="world.is_public">Public</span><span v-else>Private</span> World
      </div>

      <div class="color-text-60 mb-6">Publication Status: {{ review_status }} <Help :help="review_help" v-if="review_help"/></div>
      <div class="review-details" v-if="review_status == 'Reviewed'">
        <div class="reviewer color-text-60 mb-2">Comments by {{ review.reviewer }}:</div>
        <div class="review-text mb-4">
          <div class="review-line min-line-height"
              v-for="(line, index) in review.text.split('\n')"
              :key="index">{{ line }}</div>
          </div>
      </div>

      <div v-if="world.description" class="world-description">
        <div class="desc-line" v-for="(line, index) of descLines" :key="index">{{ line }}</div>
      </div>

      <div class="settings-actions mt-4">
        <button class="btn-small" @click="deleteWorld">DELETE</button>
        <button class="btn-small ml-4" @click="submitForReview" v-if="displaySubmitReview">SUBMIT FOR REVIEW</button>
      </div>
    </div>

    <div class="divider"></div>

    <div class="config-panels">
      <div class="advanced-config">
        <h3>ADVANCED CONFIG</h3>
        <template v-if="configData">
          <ul class="list">
            <li>World Name: {{ world.name }}</li>
            <li>Short Description: {{ world.short_description || "(empty)" }}</li>
            <li>Message of the Day: {{ world.motd || "(empty)" }}</li>
            <li>World Visibility: <span v-if="world.is_public">Public</span><span v-else>Private</span></li>

            <li>
              Starting Gold: {{ configData.starting_gold }}
            </li>

            <li>
              Starting Room:
              <router-link
                v-if="configData.starting_room"
                :to="room_link(configData.starting_room.id)"
              >{{ configData.starting_room.name }}</router-link>
              <span v-else>(unset)</span>
            </li>
            <li>
              Death Room:
              <router-link
                v-if="configData.death_room"
                :to="room_link(configData.death_room.id)"
              >{{ configData.death_room.name }}</router-link>
              <span v-else>(unset)</span>
            </li>
            <li>Death EQ Loss: {{ deathModeLabel(configData.death_mode) }}</li>
            <li>Death Route: {{ deathRouteLabel(configData.death_route) }}</li>
            <li>PvP Mode: {{ pvpModeLabel(configData.pvp_mode) }}</li>
            <li>Narrative World: {{ yesNo(configData.is_narrative) }}</li>
            <li>Can Select Core Faction: {{ yesNo(configData.can_select_faction) }}</li>
            <li>Auto Equip Items: {{ yesNo(configData.auto_equip) }}</li>
            <li>Players Can Set Title: {{ yesNo(configData.players_can_set_title) }}</li>
            <li>Allow PvP: {{ yesNo(configData.allow_pvp) }}</li>
            <li>Classless Players: {{ yesNo(configData.is_classless) }}</li>
            <li>Allow Non-ASCII Names: {{ yesNo(configData.non_ascii_names) }}</li>
            <li>Enable Channels: {{ yesNo(configData.globals_enabled) }}</li>
            <li>Decay Glory: {{ yesNo(configData.decay_glory) }}</li>
            <li>Built By: {{ configData.built_by || "(uses world author)" }}</li>
            <li>General Lobby Art: {{ configData.small_background || "(empty)" }}</li>
            <li>World Lobby Art: {{ configData.large_background || "(empty)" }}</li>
          </ul>

          <div class="config-manifest mt-6">
            <button class="btn-small" @click="copyConfigYaml">COPY CONFIG YAML</button>
            <button class="btn-thin ml-2" @click="toggleConfigYaml">
              {{ showConfigYaml ? "HIDE YAML" : "SHOW YAML" }}
            </button>
            <router-link class="ml-4" :to="edit_world_link">open Edit World</router-link>

            <pre v-if="showConfigYaml" class="config-yaml mt-4"><code>{{ configYaml }}</code></pre>
          </div>
        </template>
        <template v-else>
          <div class="color-text-60">World config is unavailable for this world.</div>
        </template>
      </div>

      <div class="world-status">
        <h3>World Admin</h3>

        <div>View connected players, start/stop multiplayer worlds.</div>

        <router-link :to="world_admin_link">manage</router-link>
      </div>

      <div class="random-profiles" v-if="!world.instance_of.id">
        <h3>RANDOM ITEM PROFILES</h3>

        <div>
          <p>Random Item Profiles offer a way to define a random load. Use cases include:</p>
          <ul class="list">
            <!-- <li>Equipping a mob with random gear</li> -->
            <li>Giving a random item reward on completing a quest</li>
            <li>Merchants with random sales inventory</li>
          </ul>
        </div>

        <router-link
          :to="{name: 'builder_world_random_profile_list', params: {world_id: $route.params.world_id}}"
        >manage</router-link>
      </div>

      <div class="transformation-templates" v-if="!world.instance_of.id">
        <h3>Transformations</h3>

        <div>
          <p>Transformations can be applied to the output of a loader rule to modify a loaded template. Use cases include:</p>
          <ul class="list">
            <li>Making a mob roam 100% of the time on tic rather than the default 5%</li>
            <li>Force a mob to roam in a particular direction</li>
            <li>Change the name of a mob when it loads</li>
            <li>Make any other one-off modification to a template for a loaded mob.</li>
          </ul>
        </div>

        <router-link
          :to="{name: 'builder_world_transformation_template_list', params: {world_id: $route.params.world_id}}"
        >manage</router-link>
      </div>

      <div class="world-builders" v-if="!world.instance_of.id">
        <h3>World Builders</h3>

        <div>Builders are able to access the editor for a given world. They can be given read-only access.</div>

        <router-link
          :to="{name: 'builder_world_builder_list', params: {world_id: $route.params.world_id}}"
        >manage</router-link>
      </div>

      <div class="world-players" v-if="!world.instance_of.id">
        <h3>World Players</h3>

        <div>View information about players in your world.</div>

        <router-link
          :to="{name: 'builder_world_player_list', params: {world_id: $route.params.world_id}}"
        >manage</router-link>
      </div>

      <div class="world-factions" v-if="!world.instance_of.id">
        <h3>Worlds Factions</h3>

        <div>View information about factions in your world.</div>

        <router-link :to="world_factions_link">manage</router-link>
      </div>

      <div class="world-facts">
        <h3>Worlds Facts</h3>

        <div>Facts are data points about your world that can be set by builders, mobs and a fact schedule. Conditions can then look at those facts to determine which loaders, room actions, quests and reactions should be considered active.</div>

        <router-link :to="world_facts_link">manage</router-link>
      </div>

      <div class='world-skills' v-if="!world.instance_of.id">
        <h3>Custom Skills</h3>

        <div>Builders can create skills, usable by players and mobs that do not have an archetype. To enable the creation of players that do not have an archetype, check the "Classless Players" checkbox under Advanced Config.</div>

        <router-link :to="world_skills_link">manage</router-link>
      </div>

      <div class="world-starting-eq" v-if="!world.instance_of.id">
        <h3>Starting EQ</h3>

        <div>Define the items that a player starts with.</div>

        <router-link :to="world_starting_eq_link">manage</router-link>
      </div>

      <div class="world-socials" v-if="!world.instance_of.id">
        <h3>SOCIALS</h3>
        <div>Socials are custom commands defined by builders that players and mobs can use to emote in a standardized way. Example typical socials: nod, shrug, wave, smile, laugh, sigh, shake, slap.</div>

        <router-link :to="world_socials_link">manage</router-link>
      </div>

      <div class="world-name-exclusions" v-if="!world.instance_of.id && configData">
        <h3>NAME EXCLUSIONS</h3>

        <div v-if="nameExclusions.length">
          {{ nameExclusions.length }} configured name exclusions.
        </div>
        <div v-else>No name exclusions configured.</div>
        <div class="color-text-60 mt-2">
          Manage exclusions through world config YAML in <router-link :to="edit_world_link">Edit World</router-link>.
        </div>
      </div>

      <div class="world-currencies" v-if="!world.instance_of.id">
        <h3>CURRENCIES</h3>

        <div>Define the currencies that players can use in your world.</div>

        <router-link
          :to="{name: 'builder_world_currency_list', params: {world_id: $route.params.world_id}}"
        >manage</router-link>
      </div>
    </div>

    <div class="instances" v-if="!world.instance_of.id">
      <div class="divider"></div>
      <h3 class='mb-8'>INSTANCES</h3>

      <p>An instance is a unique, isolated version of a game area or dungeon that a player or group can enter, allowing for a private experience separate from other players in the world.</p>

      <p>Note: Instances are currently in Alpha, proceed with caution.</p>

      <div class='my-8'>
        <button class="btn-small" @click="createInstance">CREATE INSTANCE</button>
      </div>

      <div v-for="instance in store.state.builder.worlds.instances" :key="instance.id" :instance="instance" class="mb-8">
        <a :href="instanceLink(instance.id)">{{ instance.name }}</a>
      </div>
    </div>
  </div>
  <div v-else>
    <p>You do not have permission to configure this world.</p>

    <p v-if="store.state.builder.world.builder_info.builder_assignments.length">Entites assigned to you:</p>
    <ul class='ml-4'>
      <li v-for="assignment in store.state.builder.world.builder_info.builder_assignments" :key="assignment.id">
        <router-link :to="assignment_link(assignment)">
          {{ assignment.name }}
        </router-link>
      </li>
    </ul>

  </div>
</template>

<script lang='ts' setup>
import { computed, onMounted, ref } from 'vue';
import { useStore } from 'vuex';
import { useRouter, useRoute, RouteLocationRaw } from 'vue-router';
import { capfirst } from "@/core/utils.ts";
import Help from "@/components/Help.vue";
import ReviewInstructions from "@/components/builder/world/ReviewInstructions.vue";

const store = useStore();
const router = useRouter();
const route = useRoute();

const world = computed(() => store.state.builder.world);
const configPayload = computed(() => store.state.builder.worlds.config);
const configData = computed(() => configPayload.value?.config || null);
const configYaml = computed(() => configPayload.value?.yaml || "");
const showConfigYaml = ref(false);

const room_link = (id: number) => {
  return {
    name: 'builder_room_index',
    params: {
      world_id: world.value.id,
      room_id: id,
    },
  };
};

onMounted(async () => {
  store.commit("builder/worlds/config_clear");

  // Convert each call into a promise and then call both at once

  const config_promise = store.dispatch("builder/worlds/config_fetch", {
    world_id: world.value.id,
  });

  const instances_promise = store.dispatch("builder/worlds/instances_fetch", {
    world_id: world.value.id,
  });

  await Promise.all([config_promise, instances_promise]);

});

const yesNo = (value: boolean) => value ? "Yes" : "No";

const pvpModeLabel = (value?: string) => {
  if (value === "free_for_all") return "Free for All";
  if (value === "zone") return "PvP Zones";
  if (value === "disabled") return "Disabled";
  return value || "(unset)";
};

const deathModeLabel = (value?: string) => {
  if (value === "lose_all") return "Lose All";
  if (value === "lose_none") return "Lose None";
  if (value === "lose_gold") return "Lose Gold";
  if (value === "lose_inv") return "Lose Inventory";
  if (value === "destroy_eq") return "Destroy Equipped Items";
  if (value === "lose_eq") return "Lose Equipped";
  return value || "(unset)";
};

const deathRouteLabel = (value?: string) => {
  if (value === "top_faction") return "Top Faction";
  if (value === "near_room") return "Nearest Room";
  if (value === "far_room") return "Furthest Room";
  if (value === "nearest_in_zone") return "Nearest in Zone";
  return value || "(unset)";
};

const toggleConfigYaml = () => {
  showConfigYaml.value = !showConfigYaml.value;
};

const copyConfigYaml = async () => {
  try {
    await navigator.clipboard.writeText(configYaml.value || "");
    store.commit("ui/notification_set", "World config YAML copied.");
  } catch {
    store.commit("ui/notification_set_error", "Unable to copy YAML to clipboard.");
  }
};

const createInstance = () => {
  const modal = {
    title: 'Create Instance',
    data: {
      'name': 'Unnamed Instance',
      'instance_of': world.value.id,
    },
    submitLabel: 'CREATE INSTANCE',
    schema: [
      {
        attr: 'name',
        label: 'Name',
        help: `The name of the instance.`
      },
    ],
    action: 'builder/worlds/instance_create',
  }
  store.commit('ui/modal/open_form', modal);
};

const submitForReview = () => {
  const modal = {
    title: 'Submit For Review',
    data: { 'description': '' },
    submitLabel: 'SUBMIT',
    schema: [
      {
        attr: 'description',
        label: 'Description',
        widget: 'textarea',
        help: `Describe your world to the reviewer.`
      }
    ],
    action: "builder/worlds/submit_world_for_review",
    slot: ReviewInstructions,
  };
  store.commit('ui/modal/open_form', modal);
}

const deleteWorld = async () => {
  const world_id = world.value.id;

  // Crude confirm dialog
  const c = confirm(`Are you sure you want to delete this world and everything in it? This action cannot be undone.`);
  if (!c) return;

  await store.dispatch('builder/world_delete');
  store.commit('ui/notification_set', `Deleted World ${world_id}`);
  router.push({ name: 'lobby' });
};

const edit_world_link = {
  name: 'builder_world_edit',
  params: { world_id: world.value.id },
};

const world_admin_link = {
  name: 'builder_world_admin',
  params: { world_id: world.value.id },
};

const world_factions_link = {
  name: 'builder_world_faction_list',
  params: { world_id: world.value.id },
};

const world_facts_link = {
  name: 'builder_world_fact_list',
  params: { world_id: world.value.id },
};

const world_skills_link = {
  name: 'builder_world_skill_list',
  params: { world_id: world.value.id },
};

const world_starting_eq_link = {
  name: 'builder_world_starting_eq_list',
  params: { world_id: world.value.id },
};

const world_socials_link = {
  name: 'builder_world_social_list',
  params: { world_id: world.value.id },
};

const nameExclusions = computed(() => {
  const raw = configData.value?.name_exclusions || "";
  return raw
    .split(/\r?\n/g)
    .map((name: string) => name.trim())
    .filter((name: string) => !!name);
});

const descLines = computed(() => world.value.description.split("\n"));
const displaySubmitReview = computed(() => world.value.review.status === "unsubmitted" || world.value.review.status == "reviewed");
const review = computed(() => world.value.review);

const review_status = computed(() => {
  if (world.value.review.status === 'unsubmitted') {
    return 'Unpublished';
  } else if (world.value.review.status === 'submitted') {
    return 'Under Review';
  } else if (world.value.review.status === 'reviewed') {
    return 'Reviewed';
  } else if (world.value.review.status === 'approved') {
    return 'Published';
  }
  return capfirst(world.value.review.status);
});

const review_help = computed(() => {
  if (world.value.review.status === 'unsubmitted') {
    return `A world that's been approved for publication will be featured in curated sections of the site. To initiate a review, click the SUBMIT FOR REVIEW action.`;
  } else if (world.value.review.status === 'submitted') {
    return `Your review has been submitted. Once a staff member reviews it, it will either be approved or you will receive feedback on what to change.`;
  } else if (world.value.review.status === 'reviewed') {
    return `Your world has been reviewed but is not quite ready for primetime yet. Read the reviewer's notes and re-submit it once you're ready.`;
  }
  return '';
});

const instanceLink = (instance_id) => {
  return router.resolve({
    name: 'builder_world_index',
    params: { world_id: instance_id }
  }).href;
};

const assignment_link = (assignment) => {
  if (assignment.model_type === 'room') {
    return {
      name: 'builder_room_index',
      params: {
        world_id: route.params.world_id,
        room_id: assignment.id
      }
    } as RouteLocationRaw;
  } else if (assignment.model_type === 'itemtemplate') {
    return {
      name: 'builder_item_template_details',
      params: {
        world_id: route.params.world_id,
        item_template_id: assignment.id
      }
    } as RouteLocationRaw;
  } else if (assignment.model_type === 'mobtemplate') {
    return {
      name: 'builder_mob_template_details',
      params: {
        world_id: route.params.world_id,
        mob_template_id: assignment.id
      }
    } as RouteLocationRaw;
  }
  // Assume it's a zone
  return {
    name: 'builder_zone_index',
    params: {
      world_id: route.params.world_id,
      zone_id: assignment.id
    }
  } as RouteLocationRaw;
}
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.world-description {
  div.desc-line:not(:last-child) {
    margin-bottom: 0.8em;
  }
}


.review-details {
  .review-text {
    border: 1px solid $color-background-light-border;
    padding: 15px;
  }
}

.config-manifest {
  .config-yaml {
    margin: 0;
    padding: 0.75rem;
    overflow-x: auto;
    border: 1px solid $color-form-border;
    background: $color-background;
    white-space: pre-wrap;
    word-break: break-word;

    code {
      border: 0;
      padding: 0;
      display: block;
      background: transparent;
    }
  }
}

.divider {
  margin-top: 50px;
  margin-bottom: 50px;
}
</style>
