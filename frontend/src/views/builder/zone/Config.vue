<template>
  <div id="zone-config" class="builder-config" v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <h2>ZONE CONFIG</h2>

    <div class="config-panels">
      <div class="move">
        <h3>MOVE ZONE</h3>

        <div>This action moves all rooms in a zone at once, given a certain direction and distance.</div>

        <button class="btn-small mt-2" @click="moveZone">MOVE</button>

        <div></div>
      </div>
    </div>

  </div>
  <div v-else>
    You do not have permission to configure this zone.
  </div>
</template>

<script lang='ts' setup>
import { useStore } from "vuex";
import { FormElement, DIRECTION } from "@/core/forms";

const store = useStore();

const moveZone = async () => {
  const schema: FormElement[] = [
    DIRECTION,
    {
      attr: "distance",
      label: "Distance"
    }
  ];
  const modal = {
    title: `Move Zone`,
    data: {
      direction: "north",
      distance: 0
    },
    schema: schema,
    action: "builder/zones/move_zone"
  };
  store.commit("ui/modal/open_form", modal);
}
</script>
