<template>
  <div id="world-instances" v-if="store.state.builder.world.builder_info.builder_rank > 2">
    <h1>{{ world.name.toUpperCase() }} INSTANCES</h1>

    <template v-if="isRootWorld">
      <div class="instance-actions">
        <button class="btn-small" @click="createInstance">CREATE INSTANCE</button>
      </div>

      <div class="instance-list" v-if="instances.length">
        <router-link
          v-for="instance in instances"
          :key="instance.id"
          class="instance-link"
          :to="instanceLink(instance.id)"
        >
          <span class="instance-name">{{ instance.name }}</span>
          <span class="instance-id">World {{ instance.id }}</span>
        </router-link>
      </div>
      <div v-else class="color-text-60">No instances configured.</div>
    </template>
    <template v-else>
      <div class="color-text-60">Instances are managed from the source world.</div>
    </template>
  </div>
  <div v-else>
    You do not have permission to manage instances for this world.
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted } from "vue";
import { useStore } from "vuex";
import { useRoute } from "vue-router";

const store = useStore();
const route = useRoute();

const world = computed(() => store.state.builder.world);
const instances = computed(() => store.state.builder.worlds.instances);
const isRootWorld = computed(() => !world.value.instance_of?.id);

onMounted(async () => {
  await store.dispatch("builder/worlds/instances_fetch", {
    world_id: route.params.world_id,
  });
});

const createInstance = () => {
  const modal = {
    title: "Create Instance",
    data: {
      name: "Unnamed Instance",
      instance_of: world.value.id,
    },
    submitLabel: "CREATE INSTANCE",
    schema: [
      {
        attr: "name",
        label: "Name",
        help: "The name of the instance.",
      },
    ],
    action: "builder/worlds/instance_create",
  };
  store.commit("ui/modal/open_form", modal);
};

const instanceLink = (instance_id) => {
  return {
    name: "builder_world_index",
    params: { world_id: instance_id },
  };
};
</script>

<style lang="scss" scoped>
@import "@/styles/colors.scss";

.instance-actions {
  margin: 1.5rem 0;
}

.instance-list {
  display: grid;
  gap: 0.8rem;
  max-width: 760px;
}

.instance-link {
  border: 1px solid $color-background-light-border;
  background: $color-background-light;
  color: $color-text;
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem;
  text-decoration: none;

  &:hover {
    border-color: $color-background-light-border-selected;
  }
}

.instance-name {
  color: $color-secondary;
}

.instance-id {
  color: $color-text-hex-60;
}
</style>
