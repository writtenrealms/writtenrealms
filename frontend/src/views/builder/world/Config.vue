<template>
  <div id="world-config" class="builder-config" v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <h1>{{ world.name.toUpperCase() }} CONFIG</h1>

    <div class="config-layout">
      <section class="config-links">
        <div class="top-actions">
          <router-link class="config-action" :to="editWorldPrefillLink">Edit World</router-link>
          <router-link
            v-if="baseWorld.id"
            class="config-action"
            :to="baseWorldConfigLink"
          >
            {{ baseWorld.name }} Config
          </router-link>
        </div>

        <div class="link-grid">
          <router-link
            v-for="link in visibleConfigLinks"
            :key="link.title"
            class="link-grid-item"
            :to="link.to"
          >
            <span class="link-grid-title">{{ link.title }}</span>
            <span class="link-grid-description">{{ link.description }}</span>
          </router-link>
        </div>
      </section>

      <section class="world-yaml">
        <div v-if="isConfigLoading" class="config-state color-text-60" aria-live="polite">
          Loading World YAML...
        </div>

        <div v-else-if="configLoadError" class="config-error" role="alert">
          <div>{{ configLoadError }}</div>
          <button class="btn-thin" @click="loadConfigYaml">RETRY</button>
        </div>

        <template v-else>
          <div v-if="configSubmitError" class="config-error submit-error" role="alert">
            {{ configSubmitError }}
          </div>

          <ManifestYamlEditor
            v-model="manifestText"
            :loaded-value="loadedConfigYaml"
            :is-submitting="isSubmitting"
            :min-height="500"
            copy-success-message="World YAML copied."
            copy-error-message="Unable to copy World YAML to clipboard."
            textarea-label="World YAML"
            @save="saveConfigYaml"
          >
            <template #header>
              <h2>World YAML</h2>
              <div class="color-text-60">
                Edit the current world configuration manifest.
              </div>
            </template>
          </ManifestYamlEditor>
        </template>
      </section>

      <section class="danger-zone">
        <h2>Danger Zone</h2>
        <button class="btn-small button-red" @click="deleteWorld">DELETE WORLD</button>
      </section>
    </div>
  </div>
  <div v-else>
    <p>You do not have permission to configure this world.</p>

    <p v-if="store.state.builder.world.builder_info.builder_assignments.length">Entites assigned to you:</p>
    <ul class="ml-4">
      <li v-for="assignment in store.state.builder.world.builder_info.builder_assignments" :key="assignment.id">
        <router-link :to="assignment_link(assignment)">
          {{ assignment.name }}
        </router-link>
      </li>
    </ul>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref } from "vue";
import { useStore } from "vuex";
import { useRouter, useRoute } from "vue-router";
import ManifestYamlEditor from "@/components/builder/world/ManifestYamlEditor.vue";
import { builderRoomIndexRoute, builderZoneIndexRoute } from "@/core/builderRoutes";
import { applyWorldManifest, manifestApiErrorMessage } from "@/services/manifests";

const store = useStore();
const router = useRouter();
const route = useRoute();

const world = computed(() => store.state.builder.world);
const manifestText = ref("");
const loadedConfigYaml = ref("");
const isConfigLoading = ref(true);
const isSubmitting = ref(false);
const configLoadError = ref("");
const configSubmitError = ref("");

const baseWorld = computed(() => world.value.instance_of || {});
const isRootWorld = computed(() => !baseWorld.value.id);

const editWorldPrefillLink = computed(() => ({
  name: "builder_world_edit",
  params: { world_id: route.params.world_id },
  query: { prefill: "world-config" },
}));

const baseWorldConfigLink = computed(() => ({
  name: "builder_world_config",
  params: { world_id: baseWorld.value.id },
}));

const configLinks = computed(() => [
  {
    title: "World Admin",
    description: "Connected players, maintenance mode, and spawned worlds.",
    to: {
      name: "builder_world_admin",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Item Bundles",
    description: "Weighted item definition bundles for random drops, spawn plans, and merchant stock.",
    rootOnly: true,
    to: {
      name: "builder_item_bundle_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Merchant Profiles",
    description: "Shop stock, restock rules, buyback, pricing, and purchasing funds.",
    rootOnly: true,
    to: {
      name: "builder_merchant_profile_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Craft Materials",
    description: "Salvaged resources tracked per player and spent by recipes.",
    rootOnly: true,
    to: {
      name: "builder_world_craft_material_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Crafting Recipes",
    description: "Material costs, item outputs, availability conditions, and failure messages.",
    rootOnly: true,
    to: {
      name: "builder_world_crafting_recipe_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Crafting Profiles",
    description: "Recipe collections exposed by workshop rooms and crafting NPCs.",
    rootOnly: true,
    to: {
      name: "builder_world_crafting_profile_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "World Builders",
    description: "Builder access, roles, and assigned editor work.",
    rootOnly: true,
    to: {
      name: "builder_world_builder_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "World Players",
    description: "Player records, details, and restoration tools.",
    rootOnly: true,
    to: {
      name: "builder_world_player_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Factions",
    description: "Core identities, reputation standings, ranks, and language defaults.",
    rootOnly: true,
    to: {
      name: "builder_world_faction_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Currencies",
    description: isRootWorld.value
      ? "Currency definitions, starting balances, and default money behavior."
      : `Currencies inherited from ${baseWorld.value.name}.`,
    to: {
      name: "builder_world_currency_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Socials",
    description: "Shared emote commands available to players and mobs.",
    rootOnly: true,
    to: {
      name: "builder_world_social_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Abilities",
    description: "WR2 manifest-backed combat and utility commands.",
    rootOnly: true,
    to: {
      name: "builder_world_ability_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Triggers",
    description: "WR2 command handlers and event reactions authored through YAML.",
    rootOnly: true,
    to: {
      name: "builder_world_trigger_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Instances",
    description: "Private instance contexts created from this world.",
    rootOnly: true,
    to: {
      name: "builder_world_instance_list",
      params: { world_id: route.params.world_id },
    },
  },
]);

const visibleConfigLinks = computed(() => {
  return configLinks.value
    .filter((link) => !link.rootOnly || isRootWorld.value)
    .sort((left, right) => left.title.localeCompare(right.title));
});

const setLoadedConfigYaml = (payload: any) => {
  loadedConfigYaml.value = payload?.yaml || "";
  manifestText.value = payload?.yaml || "";
  configLoadError.value = "";
};

const loadConfigYaml = async () => {
  isConfigLoading.value = true;
  configLoadError.value = "";
  configSubmitError.value = "";
  store.commit("builder/worlds/config_clear");

  try {
    const payload = await store.dispatch("builder/worlds/config_fetch", {
      world_id: world.value.id,
    });
    setLoadedConfigYaml(payload);
  } catch (error: unknown) {
    configLoadError.value = manifestApiErrorMessage(
      error,
      "Could not load World YAML.",
    );
    store.commit("ui/notification_set_error", configLoadError.value);
  } finally {
    isConfigLoading.value = false;
  }
};

const saveConfigYaml = async () => {
  const submittedYaml = manifestText.value;
  let saveCompleted = false;
  isSubmitting.value = true;
  configSubmitError.value = "";

  try {
    const response = await applyWorldManifest(
      world.value.id,
      submittedYaml,
      "world",
    );
    if (response.kind !== "world" || response.operation !== "updated") {
      throw new Error("Unexpected world manifest response.");
    }
    saveCompleted = true;
    loadedConfigYaml.value = submittedYaml;

    const [worldRefresh, configRefresh] = await Promise.allSettled([
      store.dispatch("builder/fetch_world", world.value.id),
      store.dispatch("builder/worlds/config_fetch", {
        world_id: world.value.id,
      }),
    ]);
    if (configRefresh.status === "fulfilled") {
      setLoadedConfigYaml(configRefresh.value);
    }
    if (worldRefresh.status === "rejected") throw worldRefresh.reason;
    if (configRefresh.status === "rejected") throw configRefresh.reason;
    store.commit("ui/notification_set", "World YAML saved.");
  } catch (error: unknown) {
    const fallback = saveCompleted
      ? "World YAML was saved, but its updated state could not be reloaded. Refresh this page to try again."
      : error instanceof Error && error.message === "Unexpected world manifest response."
        ? error.message
        : "Could not save World YAML.";
    configSubmitError.value = saveCompleted
      ? fallback
      : manifestApiErrorMessage(error, fallback);
    store.commit("ui/notification_set_error", configSubmitError.value);
  } finally {
    isSubmitting.value = false;
  }
};

onMounted(loadConfigYaml);

const deleteWorld = async () => {
  const world_id = world.value.id;
  const confirmed = confirm("Are you sure you want to delete this world and everything in it? This action cannot be undone.");
  if (!confirmed) return;

  await store.dispatch("builder/world_delete");
  store.commit("ui/notification_set", `Deleted World ${world_id}`);
  router.push({ name: "lobby" });
};

const assignment_link = (assignment) => {
  if (assignment.model_type === "room") {
    return builderRoomIndexRoute(route.params.world_id, assignment);
  }
  return builderZoneIndexRoute(route.params.world_id, assignment);
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.config-layout {
  max-width: $site-max-width;
  width: 100%;
}

.config-links,
.world-yaml,
.danger-zone {
  margin-top: 2rem;
}

.top-actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  justify-content: flex-start;
  margin-bottom: 1rem;
}

.config-action {
  background: transparent;
  border: 0;
  color: $color-primary;
  cursor: pointer;
  font: inherit;
  line-height: inherit;
  padding: 0;
  text-decoration: none;

  &:hover {
    color: $color-primary;
    text-decoration: underline;
  }

}

.link-grid {
  gap: 0.8rem;
}

.config-state {
  align-items: center;
  display: flex;
  justify-content: center;
  min-height: 12rem;
}

.config-error {
  border: 1px solid $color-form-border;
  padding: 1rem;

  .btn-thin {
    margin-top: 0.75rem;
  }
}

.submit-error {
  margin-bottom: 1rem;
}

.danger-zone {
  border-top: 1px solid $color-background-light-border;
  padding-top: 1.25rem;

  h2 {
    color: $color-text-hex-60;
    margin-bottom: 1rem;
  }
}
</style>
