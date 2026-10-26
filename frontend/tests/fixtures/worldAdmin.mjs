export const rootWorld = { id: 12, name: 'Phalanx', builder_info: { builder_rank: 4 }, last_viewed_room: null };
export function worldInstance() {
  const now = new Date().toISOString();
  return {
    id: 13, key: 'world.13', is_multiplayer: true, parent_world: null, instance_ref: null, leader: null,
    lifecycle_details: {
      current: 'running', changed_at: now, last_spawn_plan_run_ts: now,
      last_extraction_ts: null, last_entered_ts: null, last_played_ts: null, cleanup_started_ts: null,
    },
    recovery_actions: { recover_to_stopped: false },
    counts: {
      mobs_loaded: 27, mobs_pending_deletion: 0, items_on_ground: 0, items_total: 66, items_pending_deletion: 8,
      players_logged_in: 1, player_records: 4, instance_assignments: 0,
      items_by_container_type: { rooms: 0, players: 22, mobs: 27, inside_items: 0, equipment: 17, without_container: 0 },
    },
    spawn_plan_details: { last_run_ts: now, configured_spawn_plan_count: 12, configured_spawn_entry_count: 48 },
    active_players: [{ id: 1, name: 'Onyx', is_builder: true, room: { name: 'The agora' }, last_connection_ts: now, last_action_ts: now }],
    world_state: { gates_open: true, patrols_defeated: 3 },
  };
}
