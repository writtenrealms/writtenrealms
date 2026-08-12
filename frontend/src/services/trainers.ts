import axios from "axios";
import type {
  BuilderEntityId,
  BuilderWorldId,
  ManifestBackedDetail,
} from "@/services/manifests";

export interface TrainerProfileSummary {
  id: number;
  key: string;
  slug: string;
  name: string;
  notes: string;
  model_type: string;
  modified_ts: string;
  ability_count: number;
  learning?: TrainerProfileLearningPolicy;
}

export interface TrainerProfileLearningPolicy {
  conditions?: unknown;
  max_known?: number | "uncapped" | null;
}

export interface TrainerProfileAbility {
  id: number;
  key: string;
  slug: string;
  name: string;
  order: number;
}

export interface TrainerProfileDetail
  extends TrainerProfileSummary,
    ManifestBackedDetail {
  abilities: TrainerProfileAbility[];
}

const worldEndpoint = (worldId: BuilderWorldId) => `/builder/worlds/${worldId}`;

export const trainerProfileListEndpoint = (worldId: BuilderWorldId) => (
  `${worldEndpoint(worldId)}/trainerprofiles/`
);

export const trainerProfileDetailEndpoint = (
  worldId: BuilderWorldId,
  profileId: BuilderEntityId,
) => `${trainerProfileListEndpoint(worldId)}${profileId}/`;

export const fetchTrainerProfile = async (
  worldId: BuilderWorldId,
  profileId: BuilderEntityId,
): Promise<TrainerProfileDetail> => {
  const response = await axios.get<TrainerProfileDetail>(
    trainerProfileDetailEndpoint(worldId, profileId),
  );
  return response.data;
};
