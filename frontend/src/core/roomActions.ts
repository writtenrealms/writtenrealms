export const applyRoomActions = (room: any, worldId: number | string, payload: any) => {
  if (!room || !payload || String(payload.world_id) !== String(worldId)
    || payload.room_key !== room.key || !Array.isArray(payload.actions)
    || !Number.isFinite(payload.actions_revision)
    || payload.actions_revision < Number(room.actions_revision || 0)) return room;
  return { ...room, actions: [...payload.actions], actions_revision: payload.actions_revision };
};
