import { useRoute, useRouter } from "vue-router";
import { useStore } from "vuex";

// Entering the world as a character, shared by the lobby hero and the
// character list. Characters that must be transferred first go through
// the transfer flow instead.
export function useCharEntry() {
  const store = useStore();
  const route = useRoute();
  const router = useRouter();

  const needsTransfer = (char) =>
    char.can_transfer && !store.state.auth.user.is_temporary;

  // Pages outside a world's lobby (e.g. a character page) pass the world id.
  const playChar = (char, worldId = route.params.world_id) => {
    if (needsTransfer(char)) {
      router.push({
        name: 'lobby_world_transfer',
        params: { player_id: char.id, world_id: worldId },
      });
      return;
    }
    store.dispatch('game/request_enter_world', {
      player_id: char.id,
      world_id: worldId,
    });
  };

  return { needsTransfer, playChar };
}
