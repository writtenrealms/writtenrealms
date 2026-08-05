<template>
  <EditableCollection
    title="Room Action"
    name="room_actions"
    registration_name="room_actions"
    :endpoint="endpoint"
    :display_component="display_component"
    :schema="schema"
  >
    <div class="color-text-60">
      Room actions are custom commands defined per room. For more information, refer to the <a href="https://docs.writtenrealms.com/builders/room-actions" target="_blank" rel="noopener noreferrer">room action guide</a>.
    </div>
  </EditableCollection>
</template>

<script lang='ts' setup>
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import RoomActionDetails from "@/components/builder/room/RoomActionDetails.vue";
import EditableCollection from "@/components/editablecollection/EditableCollection.vue";
import { BUILDER_FORMS } from "@/core/forms.ts";

const route = useRoute();
const store = useStore();
const endpoint = `/builder/worlds/${route.params.world_id}/rooms/${store.state.builder.room.id}/actions/`;
const display_component = RoomActionDetails;
const schema = BUILDER_FORMS.ROOM_ACTION;
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
</style>
