<template>
  <div class="map-wrapper mx-0">
    <Map
      v-if="map"
      :unit="8"
      :radius="radius"
      :map="map"
      :center_key="center_key"
      @clickRoom="onRoomClick"
    />
  </div>
</template>

<script lang='ts' setup>
import { computed } from "vue";
import { useStore } from "vuex";
import Map from "@/components/ui/Map.vue";

const store = useStore();

defineProps<{
  radius: number;
}>();

const map = computed(() => store.state.game.map);
const room_key = computed(() => store.state.game.room_key);
const room: any = computed(() => store.state.game.room);
const center_key = computed(() => {
  if (map.value && room_key.value && map.value[room_key.value]) {
    return room_key.value;
  }
  if (map.value && room.value && room.value.key && map.value[room.value.key]) {
    return room.value.key;
  }
  if (map.value) {
    const map_keys = Object.keys(map.value);
    if (map_keys.length) return map_keys[0];
  }
  return null;
});

const onRoomClick = (clickedRoom) => {
  const current_x = room.value.x,
        current_y = room.value.y;

  if (clickedRoom.x === current_x && clickedRoom.y === current_y + 1) {
    store.dispatch("game/cmd", "north");
    return;
  } else if (clickedRoom.x === current_x && clickedRoom.y === current_y - 1) {
    store.dispatch("game/cmd", "south");
    return;
  } else if (clickedRoom.y === current_y && clickedRoom.x === current_x + 1) {
    store.dispatch("game/cmd", "east");
    return;
  } else if (clickedRoom.y === current_y && clickedRoom.x === current_x - 1) {
    store.dispatch("game/cmd", "west");
    return;
  }
}
</script>

<style lang='scss'>
@import "@/styles/colors.scss";

.map-wrapper {
  // margin: 0 auto;
  > .map-view {
    > #map {
      background: $color-background;
      margin: 0 auto;
      display: block;
      border: 0;
    }
  }
}
</style>
