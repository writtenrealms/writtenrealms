<template>
  <div id="room-spawn-plans">
    <h2>SPAWN PLANS</h2>

    <template v-if="fetched && spawnPlans.length">
      <div class="spawn-plan mb-8" v-for="spawnPlan in spawnPlans" :key="spawnPlan.id">
        <h3>{{ spawnPlan.name || spawnPlan.slug }}</h3>
        <div class="color-text-60">
          {{ spawnPlan.num_entries }} entries
          <span v-if="spawnPlan.respawn_mode">/ {{ spawnPlan.respawn_mode }}</span>
        </div>
        <div v-if="spawnPlan.matching_entries && spawnPlan.matching_entries.length">
          Matching entries:
          <ul class="list">
            <li v-for="entry in spawnPlan.matching_entries" :key="entry">{{ entry }}</li>
          </ul>
        </div>
        <router-link :to="editWorldLink">Edit spawn plan YAML</router-link>
      </div>
    </template>
    <div v-else-if="fetched">No spawn plans target this room.</div>
  </div>
</template>

<script lang='ts' setup>
import { ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import { useStore } from "vuex";
import axios from "axios";

const route = useRoute();
const store = useStore();

const spawnPlans = ref<any[]>([]);
const fetched = ref(false);

onMounted(async () => {
  const resp = await axios.get(
    `/builder/worlds/${route.params.world_id}/rooms/${store.state.builder.room.id}/spawn-plans/`
  );
  spawnPlans.value = resp.data.spawn_plans || [];
  fetched.value = true;
});

const editWorldLink = {
  name: 'builder_world_edit',
  params: {
    world_id: route.params.world_id,
  },
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";
#room-spawn-plans {
  h2 {
    margin-bottom: 20px;
  }

  h3 {
    text-transform: uppercase;
    margin-bottom: 10px;
  }

  li > a {
    color: inherit;
  }
}
</style>
