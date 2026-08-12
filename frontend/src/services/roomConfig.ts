import axios from "axios";
import type {
  BuilderEntityId,
  BuilderWorldId,
} from "@/services/manifests";

export interface BuilderReference {
  id: number;
  key: string;
  name: string;
  slug?: string;
}

export interface RoomConfigPayload {
  has_instances?: boolean;
  transfer_to?: BuilderReference | null;
  transfer_to_world?: BuilderReference | null;
  merchant_profile?: BuilderReference | null;
  merchant_profile_world?: BuilderReference | null;
  trainer_profile?: BuilderReference | null;
  trainer_profile_world?: BuilderReference | null;
  can_edit?: boolean;
  can_edit_training?: boolean;
}

export const roomConfigEndpoint = (
  worldId: BuilderWorldId,
  roomId: BuilderEntityId,
) => `/builder/worlds/${worldId}/rooms/${roomId}/config/`;

export const fetchRoomConfig = async (
  worldId: BuilderWorldId,
  roomId: BuilderEntityId,
): Promise<RoomConfigPayload> => {
  const response = await axios.get<RoomConfigPayload>(
    roomConfigEndpoint(worldId, roomId),
  );
  return response.data;
};

export const updateRoomMerchantProfile = async (
  worldId: BuilderWorldId,
  roomId: BuilderEntityId,
  merchantProfileId: number | null,
): Promise<RoomConfigPayload> => {
  const response = await axios.patch<RoomConfigPayload>(
    roomConfigEndpoint(worldId, roomId),
    { merchant_profile: merchantProfileId },
  );
  return response.data;
};

export const updateRoomTrainerProfile = async (
  worldId: BuilderWorldId,
  roomId: BuilderEntityId,
  trainerProfileId: number | null,
): Promise<RoomConfigPayload> => {
  const response = await axios.patch<RoomConfigPayload>(
    roomConfigEndpoint(worldId, roomId),
    { trainer_profile: trainerProfileId },
  );
  return response.data;
};
