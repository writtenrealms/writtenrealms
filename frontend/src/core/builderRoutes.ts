import type { RouteLocationRaw } from "vue-router";

type RoomIdentity = {
  id?: number | string | null;
  relative_id?: number | string | null;
  manifest_ref?: string | null;
  ref?: string | null;
};

type ZoneIdentity = {
  id?: number | string | null;
  relative_id?: number | string | null;
  manifest_ref?: string | null;
  ref?: string | null;
};

export const zoneRelativeId = (zone: ZoneIdentity | null | undefined): string | null => {
  const directValue = zone?.relative_id;
  if (directValue !== undefined && directValue !== null && String(directValue).trim()) {
    return String(directValue);
  }

  const match = String(zone?.manifest_ref || zone?.ref || "").match(/^zone@(\d+)$/);
  return match ? match[1] : null;
};

export const zoneRelativeIdFromRef = (value: unknown): string | null => {
  const match = String(value || "").match(/^zone@(\d+)$/);
  return match ? match[1] : null;
};

export const isBuilderZoneContextRoute = (
  routeName: unknown,
  relativeId: unknown,
): boolean => (
  String(routeName || "").startsWith("builder_zone_")
  && relativeId !== undefined
  && relativeId !== null
  && String(relativeId).trim() !== ""
);

export const builderZoneIndexRoute = (
  worldId: number | string | string[],
  zone: ZoneIdentity,
): RouteLocationRaw => {
  const relativeId = zoneRelativeId(zone);
  if (relativeId) {
    return {
      name: "builder_zone_index",
      params: {
        world_id: worldId,
        zone_relative_id: relativeId,
      },
    };
  }

  return {
    name: "builder_zone_database_lookup",
    params: {
      world_id: worldId,
      zone_database_id: zone.id,
    },
  };
};

export const roomRelativeId = (room: RoomIdentity | null | undefined): string | null => {
  const directValue = room?.relative_id;
  if (directValue !== undefined && directValue !== null && String(directValue).trim()) {
    return String(directValue);
  }

  const match = String(room?.manifest_ref || room?.ref || "").match(/^room@(\d+)$/);
  return match ? match[1] : null;
};

export const roomRelativeIdFromRef = (value: unknown): string | null => {
  const match = String(value || "").match(/^room@(\d+)$/);
  return match ? match[1] : null;
};

export const isBuilderRoomContextRoute = (
  routeName: unknown,
  relativeId: unknown,
): boolean => (
  String(routeName || "").startsWith("builder_room_")
  && relativeId !== undefined
  && relativeId !== null
  && String(relativeId).trim() !== ""
);

export const builderRoomDetailEndpoint = (
  worldId: number | string,
  room: RoomIdentity | null | undefined,
): string | null => {
  const relativeId = roomRelativeId(room);
  if (relativeId) {
    return `/builder/worlds/${worldId}/rooms/by-relative-id/${relativeId}/`;
  }

  if (room?.id !== undefined && room.id !== null && String(room.id).trim()) {
    return `/builder/worlds/${worldId}/rooms/${room.id}/`;
  }

  return null;
};

export const builderRoomIndexRoute = (
  worldId: number | string | string[],
  room: RoomIdentity,
): RouteLocationRaw => {
  const relativeId = roomRelativeId(room);
  if (relativeId) {
    return {
      name: "builder_room_index",
      params: {
        world_id: worldId,
        room_relative_id: relativeId,
      },
    };
  }

  return {
    name: "builder_room_database_lookup",
    params: {
      world_id: worldId,
      room_database_id: room.id,
    },
  };
};
