<template>
  <EditableCollection
    title="Zone Procession"
    :endpoint="endpoint"
    :display_component="display_component"
    :schema="schema"
    registration_name='zone_processions'
  />
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import ZoneProcessionDetail from "./ProcessionDetails.vue";
import { FormElement } from "@/core/forms.ts";
import EditableCollection from "@/components/editablecollection/EditableCollection.vue";

const store = useStore();

const endpoint = computed(() => (
  `/builder/worlds/${store.state.builder.world.id}/zones/${store.state.builder.zone.id}/processions/`
));
const display_component = ZoneProcessionDetail;
const schema: FormElement[] = [
  {
    attr: "room",
    label: "Room",
    widget: "reference",
    references: "room",
    context: "zone"
  },
  {
    attr: "faction",
    label: "Faction",
    widget: "reference",
    references: "faction"
  }
];
</script>
