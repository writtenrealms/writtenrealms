<template>
  <div v-if="store.state.builder.world.instance_of.id">
    <h2 class="mb-4">MOB TEMPLATES</h2>
    <p>The mob templates of an instance are inherited from the parent world:</p>
    <p>
      <router-link :to="{name: 'builder_mob_template_list', params: {world_id: store.state.builder.world.instance_of.id}}">
        {{ store.state.builder.world.instance_of.name }} Mob Templates
      </router-link>
    </p>
  </div>

  <ElementList
    v-else
    title="Mob Templates"
    :schema="list_schema"
    :filters="list_filters"
    :endpoint="endpoint"
    :resolve_route="resolve_route"
    filter-display="dropdown"
    table-variant="data"
    default-sort="-modified_ts"
    @add="onClickAdd"
  />
</template>

<script lang="ts" setup>
import { onMounted, ref, computed } from "vue";
import { useStore } from "vuex";
import { useRoute, onBeforeRouteUpdate } from "vue-router";
import ElementList from "@/components/elementlist/ElementList.vue";
import { GET_MOB_TEMPLATE_INFO } from "@/core/forms.ts";
import { formatRelativeModifiedDate } from "@/core/utils.ts";
import axios from "axios";

const store = useStore();
const route = useRoute();

const world_factions = ref<any[]>([]);


// import { BUILDER_ACTIONS, UI_MUTATIONS } from "@/constants";

const endpoint = `/builder/worlds/${route.params.world_id}/mobtemplates/`;

onMounted(async () => {
  const resp = await axios.get(
    `/builder/worlds/${route.params.world_id}/factions/`
  );
  const factions: {}[] = [];
  for (const faction of resp.data.results) {
    factions.push({
      key: faction.code,
      name: faction.name
    });
  }
  world_factions.value = factions;
});

const resolve_route = element => {
  return {
    name: 'builder_mob_template_details',
    params: {
      world_id: store.state.builder.world.id,
      mob_template_id: element.id
    }
  };
};

const list_schema: any[] = [
  { name: "id", label: "ID", sortable: true },
  { name: "name", label: "Name", nowrap: true, sortable: true },
  { name: "level", label: "Level", light: true, sortable: true },
  {
    name: "modified_ts",
    label: "Modified",
    nowrap: true,
    sortable: true,
    format: formatRelativeModifiedDate
  }
];

const list_filters = computed(() => {
  const mob_types = {
    label: "Type",
    attr: "type",
    filter_options: [
      { key: "humanoid", name: "humanoid" },
      { key: "aberration", name: "aberration" },
      { key: "beast", name: "beast" },
      { key: "celestial", name: "celestial" },
      { key: "construct", name: "construct" },
      { key: "dragon", name: "dragon" },
      { key: "elemental", name: "elemental" },
      { key: "fey", name: "fey" },
      { key: "fiend", name: "fiend" },
      { key: "giant", name: "giant" },
      { key: "monstrosity", name: "monstrosity" },
      { key: "ooze", name: "ooze" },
      { key: "plant", name: "plant" },
      { key: "undead", name: "undead" }
    ]
  };

  const factions = {
    label: "Faction",
    attr: "faction",
    filter_options: world_factions.value
  };

  return [
    {
      label: "Special",
      attr: "special",
      filter_options: [
        { key: "is_merchant", name: "Is Merchant" },
        { key: "has_quest", name: "Has Quest" },
        { key: "is_elite", name: "Is Elite" }
      ]
    },
    factions,
    mob_types,
  ];
});

const onClickAdd = () => {
  const default_archetype = store.state.builder.world.is_classless ? "" : "warrior";
  const new_mob_template = {
    name: "an unnamed mob",
    level: 1,
    description: "",
    notes: "",
    room_description: "",
    keywords: "",
    type: "humanoid",
    archetype: default_archetype,
    hit_msg_first: "hit",
    hit_msg_third: "hits",
    aggression: "normal",
    gender: "female",
    is_invisible: false,
    core_faction: store.getters["builder/defaultCoreFaction"],
    fights_back: true
  };
  const schema = GET_MOB_TEMPLATE_INFO();

  store.commit('ui/modal/open_form', {
    title: `Add Mob Template`,
    data: new_mob_template,
    schema: schema,
    action: "builder/worlds/mob_template_create"
  });
};

onBeforeRouteUpdate(async (to, from, next) => {
  if (to.params.world_id !== from.params.world_id) {
    const world = await store.dispatch(
      'builder/fetch_world',
      to.params.world_id);
    store.commit('builder/room_set', world.last_viewed_room);
    store.commit('builder/zone_set', world.last_viewed_room.zone);
  }
  next();
});
</script>
