/** Apply an expiration only to the live room in the event's runtime. */
export function applyItemExpiration(state: any, payload: any): void {
  if (!payload || !Array.isArray(payload.item_keys)
    || Number(state.world?.id) !== Number(payload.runtime_world_id)
    || Number(state.room?.id) !== Number(payload.room_id)) return;

  const keys = new Set(payload.item_keys);
  const remove = (inventory: any[]): any[] => inventory
    .filter(item => !keys.has(item.key))
    .map(item => Array.isArray(item.inventory)
      ? { ...item, inventory: remove(item.inventory) } : item);
  // Replace live state rather than mutating items shared with console history.
  state.room = { ...state.room, inventory: remove(state.room.inventory || []) };
}
