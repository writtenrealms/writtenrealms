<template>
  <div id="world-config" class="builder-config" v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <h1>{{ world.name.toUpperCase() }} CONFIG</h1>

    <div class="config-layout">
      <section class="config-links">
        <div class="top-actions">
          <router-link class="config-action" :to="editWorldPrefillLink">Edit World</router-link>
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

      <section class="world-data">
        <div class="section-header">
          <h2>World Data</h2>
          <div class="data-actions">
            <button class="btn-small" @click="copyConfigYaml">COPY YAML</button>
            <button class="btn-thin" @click="toggleConfigYaml">
              {{ showConfigYaml ? "HIDE YAML" : "SHOW YAML" }}
            </button>
          </div>
        </div>

        <div class="world-data-layout">
          <div class="manifest-grid">
            <template v-if="worldSpec">
              <section
                v-for="section in manifestSections"
                :key="section.title"
                class="manifest-section"
                :class="{ wide: section.wide }"
              >
                <h3>{{ section.title }}</h3>

                <table v-if="section.tableEntries.length" class="data-table key-value-table manifest-section-table">
                  <tbody>
                    <tr v-for="entry in section.tableEntries" :key="entry.key">
                      <th scope="row">{{ entry.label }}</th>
                      <td>
                        <router-link v-if="entry.to" :to="entry.to">
                          {{ entry.displayValue }}
                        </router-link>
                        <ManifestValue v-else :value="entry.value" />
                      </td>
                    </tr>
                  </tbody>
                </table>

                <div
                  v-for="entry in section.stackedEntries"
                  :key="entry.key"
                  class="manifest-field"
                >
                  <div class="manifest-label">{{ entry.label }}</div>
                  <div class="manifest-field-value">
                    <router-link v-if="entry.to" :to="entry.to">
                      {{ entry.displayValue }}
                    </router-link>
                    <template v-else-if="entry.backgroundPreview">
                      <ManifestValue :value="entry.value" />
                      <details class="background-preview">
                        <summary>Preview background</summary>
                        <img :src="entry.backgroundPreview" :alt="`${entry.label} preview`">
                      </details>
                    </template>
                    <ManifestValue
                      v-else
                      :value="entry.value"
                      :collapse-complex="entry.collapseComplex"
                    />
                  </div>
                </div>
              </section>
            </template>
            <template v-else>
              <div class="color-text-60">World config is unavailable for this world.</div>
            </template>
          </div>

          <aside class="config-yaml-panel" v-if="showConfigYaml">
            <div class="yaml-panel-header">
              <h3>World YAML</h3>
            </div>
            <pre class="config-yaml"><code>{{ configYaml }}</code></pre>
          </aside>
        </div>
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
import { useRouter, useRoute, RouteLocationRaw } from "vue-router";
import ManifestValue from "@/components/builder/world/ManifestValue.vue";

const store = useStore();
const router = useRouter();
const route = useRoute();

const world = computed(() => store.state.builder.world);
const configPayload = computed(() => store.state.builder.worlds.config);
const configData = computed(() => configPayload.value?.config || null);
const configYaml = computed(() => configPayload.value?.yaml || "");
const worldSpec = computed(() => configPayload.value?.manifest?.spec || null);
const showConfigYaml = ref(false);

const isRootWorld = computed(() => !world.value.instance_of?.id);

const editWorldPrefillLink = computed(() => ({
  name: "builder_world_edit",
  params: { world_id: route.params.world_id },
  query: { prefill: "world-config" },
}));

const roomLinkForKey = (key: string) => {
  const room = configData.value?.[key];
  if (!room?.id) return null;
  return {
    name: "builder_room_index",
    params: {
      world_id: route.params.world_id,
      room_id: room.id,
    },
  };
};

const roomDisplayForKey = (key: string, fallback: any) => {
  const room = configData.value?.[key];
  if (room?.name) return room.name;
  return fallback || "(unset)";
};

const backgroundPreviewSrc = (value: any) => {
  if (typeof value !== "string") return "";
  const src = value.trim();
  if (!src) return "";
  return src;
};

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
    title: "Random Item Profiles",
    description: "Reusable item roll profiles for rewards, merchants, and loads.",
    rootOnly: true,
    to: {
      name: "builder_world_random_profile_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Transformations",
    description: "One-off template changes applied through loader rules.",
    rootOnly: true,
    to: {
      name: "builder_world_transformation_template_list",
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
    title: "Starting EQ",
    description: "Items granted to new players when they enter the world.",
    rootOnly: true,
    to: {
      name: "builder_world_starting_eq_list",
      params: { world_id: route.params.world_id },
    },
  },
  {
    title: "Currencies",
    description: "Currency definitions and default money behavior.",
    rootOnly: true,
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
  return configLinks.value.filter((link) => !link.rootOnly || isRootWorld.value);
});

const manifestGroups = [
  {
    title: "Identity",
    keys: ["name", "short_description", "description", "motd", "is_public", "built_by"],
  },
  {
    title: "Progression",
    keys: ["starting_level", "max_level", "starting_gold", "leveling_curve", "ability_progression"],
  },
  {
    title: "Rooms",
    keys: ["starting_room", "death_room"],
  },
  {
    title: "Combat",
    keys: ["combat_resolution_interval", "combat", "death_mode", "death_route"],
    wide: true,
  },
  {
    title: "PvP",
    keys: ["allow_pvp", "pvp_mode"],
  },
  {
    title: "Player Rules",
    keys: [
      "can_select_faction",
      "auto_equip",
      "is_narrative",
      "players_can_set_title",
      "is_classless",
      "non_ascii_names",
      "globals_enabled",
      "decay_glory",
    ],
  },
  {
    title: "Presentation",
    keys: ["small_background", "large_background"],
  },
  {
    title: "Naming",
    keys: ["name_exclusions"],
  },
  {
    title: "Stats",
    keys: ["stats"],
    wide: true,
  },
];

const labelOverrides = {
  motd: "Message Of The Day",
  is_public: "Visibility",
  pvp_mode: "PvP Mode",
  allow_pvp: "Allow PvP",
  small_background: "General Lobby Art",
  large_background: "World Lobby Art",
};

const labelForKey = (key: string) => {
  if (labelOverrides[key]) return labelOverrides[key];
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const isPlainObject = (value: any) => {
  return value !== null && typeof value === "object" && !Array.isArray(value);
};

const isEmptyValue = (value: any) => {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (isPlainObject(value)) return Object.keys(value).length === 0;
  return false;
};

const isCompactPrimitive = (value: any) => {
  if (isEmptyValue(value) || typeof value === "number" || typeof value === "boolean") return true;
  if (typeof value !== "string") return false;
  return value.length <= 160 && !value.includes("\n");
};

const isPrimitiveArray = (value: any) => {
  return Array.isArray(value) && value.every((item) => isCompactPrimitive(item));
};

const isSimpleSectionEntry = (entry: any) => {
  if (entry.backgroundPreview || entry.collapseComplex) return false;
  if (entry.to) return true;
  if (isCompactPrimitive(entry.value) || isPrimitiveArray(entry.value)) return true;
  return false;
};

const entryForKey = (spec: any, key: string) => {
  const value = spec[key];
  const entry: any = {
    key,
    label: labelForKey(key),
    value,
  };

  if (key === "starting_room" || key === "death_room") {
    entry.to = roomLinkForKey(key);
    entry.displayValue = roomDisplayForKey(key, value);
  }

  if (key === "small_background" || key === "large_background") {
    entry.backgroundPreview = backgroundPreviewSrc(value);
  }

  if (key === "combat" || key === "stats") {
    entry.collapseComplex = true;
  }

  return entry;
};

const manifestSections = computed(() => {
  const spec = worldSpec.value;
  if (!spec) return [];

  const usedKeys = new Set<string>();
  const sections = manifestGroups
    .map((group) => {
      const entries = group.keys
        .filter((key) => Object.prototype.hasOwnProperty.call(spec, key))
        .map((key) => {
          usedKeys.add(key);
          return entryForKey(spec, key);
        });

      return {
        title: group.title,
        entries,
        tableEntries: entries.filter(isSimpleSectionEntry),
        stackedEntries: entries.filter((entry) => !isSimpleSectionEntry(entry)),
        wide: group.wide,
      };
    })
    .filter((section) => section.entries.length);

  const remainingEntries = Object.keys(spec)
    .filter((key) => !usedKeys.has(key))
    .map((key) => entryForKey(spec, key));

  if (remainingEntries.length) {
    sections.push({
      title: "Other",
      entries: remainingEntries,
      tableEntries: remainingEntries.filter(isSimpleSectionEntry),
      stackedEntries: remainingEntries.filter((entry) => !isSimpleSectionEntry(entry)),
      wide: false,
    });
  }

  return sections;
});

onMounted(async () => {
  store.commit("builder/worlds/config_clear");
  await store.dispatch("builder/worlds/config_fetch", {
    world_id: world.value.id,
  });
});

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
    return {
      name: "builder_room_index",
      params: {
        world_id: route.params.world_id,
        room_id: assignment.id,
      },
    } as RouteLocationRaw;
  } else if (assignment.model_type === "itemtemplate") {
    return {
      name: "builder_item_template_details",
      params: {
        world_id: route.params.world_id,
        item_template_id: assignment.id,
      },
    } as RouteLocationRaw;
  } else if (assignment.model_type === "mobtemplate") {
    return {
      name: "builder_mob_template_details",
      params: {
        world_id: route.params.world_id,
        mob_template_id: assignment.id,
      },
    } as RouteLocationRaw;
  }
  return {
    name: "builder_zone_index",
    params: {
      world_id: route.params.world_id,
      zone_id: assignment.id,
    },
  } as RouteLocationRaw;
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
@import "@/styles/layout.scss";

.config-layout {
  max-width: $site-max-width;
  width: 100%;
}

.section-header,
.yaml-panel-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;

  @media ($mobile-site) {
    align-items: flex-start;
    flex-direction: column;
  }
}

.config-links,
.world-data,
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

.data-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.danger-zone {
  border-top: 1px solid $color-background-light-border;
  padding-top: 1.25rem;

  h2 {
    color: $color-text-hex-60;
    margin-bottom: 1rem;
  }
}

.world-data-layout {
  display: grid;
  gap: 1.5rem;
  min-width: 0;
}

.manifest-grid {
  display: grid;
  gap: 1.5rem;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  min-width: 0;
}

.manifest-section {
  border-top: 1px solid $color-background-light-border;
  min-width: 0;
  padding: 1.25rem 0;

  &.wide {
    grid-column: 1 / -1;
  }

  h3 {
    color: $color-secondary;
    margin-bottom: 1rem;
  }
}

.manifest-field {
  margin-bottom: 1.25rem;
  min-width: 0;
}

.manifest-section-table + .manifest-field {
  margin-top: 1.25rem;
}

.manifest-label {
  color: $color-text-hex-60;
  margin-bottom: 0.35rem;
}

.manifest-field-value {
  line-height: 1.5;
  min-width: 0;
}

.background-preview {
  margin-top: 0.5rem;

  summary {
    color: $color-secondary;
    cursor: pointer;
    width: fit-content;
  }

  img {
    border: 1px solid $color-background-light-border;
    display: block;
    margin-top: 0.75rem;
    max-height: 280px;
    max-width: 100%;
    object-fit: contain;
  }
}

.config-yaml-panel {
  border-top: 1px solid $color-background-light-border;
  min-width: 0;
  order: -1;
  padding-top: 1.25rem;
  width: 100%;
}

.config-yaml {
  background: $color-background;
  border: 1px solid $color-form-border;
  box-sizing: border-box;
  margin: 0;
  max-height: 70vh;
  overflow: auto;
  padding: 1rem;
  white-space: pre;
  width: 100%;

  code {
    background: transparent;
    border: 0;
    display: block;
    padding: 0;
  }
}

.manifest-section.wide :deep(.manifest-map.root) {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.manifest-section.wide :deep(.manifest-map.nested) {
  grid-template-columns: 1fr;
}
</style>
