import axios from "axios";
import type { Money } from "@/core/economy.ts";

export type BuilderWorldId = string | number;
export type BuilderEntityId = string | number;
export type ManifestDocument = Record<string, unknown>;

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface CraftingListEntity {
  id: number;
  key: string;
  slug: string;
  name: string;
  model_type: string;
  modified_ts: string;
}

export interface CraftMaterial extends CraftingListEntity {
  description: string;
  order: number;
}

export interface ItemDefinitionReference {
  id: number;
  key: string;
  slug: string;
  name: string;
}

export interface CraftingRecipeSummary extends CraftingListEntity {
  group: string;
  order: number;
  cost: number | null;
  currency: string | number | { code?: string; name?: string; plural_name?: string } | null;
  money: Money | null;
  conditions: Record<string, unknown>;
  failure_message: string;
  output_item_definition: ItemDefinitionReference;
  ingredient_count: number;
}

export interface CraftingIngredient {
  material: string;
  quantity: number;
}

export interface CraftingProfileSummary extends CraftingListEntity {
  keywords: string;
  recipe_count: number;
}

export interface ManifestBackedDetail {
  manifest: ManifestDocument;
  yaml: string;
  delete_manifest: ManifestDocument;
  delete_yaml: string;
}

export type CraftMaterialDetail = CraftMaterial & ManifestBackedDetail;

export interface CraftingRecipeDetail
  extends Omit<CraftingRecipeSummary, "ingredient_count">,
    ManifestBackedDetail {
  inputs: CraftingIngredient[];
}

export interface CraftingProfileDetail
  extends CraftingProfileSummary,
    ManifestBackedDetail {
  recipes: string[];
}

export type CraftingManifestKind =
  | "craftmaterial"
  | "craftingrecipe"
  | "craftingprofile";

export interface CraftingManifestEntity {
  id: number;
  key: string;
  slug: string;
  name: string;
}

export type CraftMaterialManifestPayload =
  | Omit<CraftMaterialDetail, "model_type" | "modified_ts">
  | CraftingManifestEntity;

export type CraftingRecipeManifestPayload =
  | Omit<CraftingRecipeDetail, "model_type" | "modified_ts">
  | CraftingManifestEntity;

export type CraftingProfileManifestPayload =
  | Omit<CraftingProfileDetail, "model_type" | "modified_ts" | "recipe_count">
  | CraftingManifestEntity;

export interface CraftingManifestApplyResponse {
  kind: CraftingManifestKind;
  operation: "created" | "updated" | "deleted";
  craft_material?: CraftMaterialManifestPayload;
  crafting_recipe?: CraftingRecipeManifestPayload;
  crafting_profile?: CraftingProfileManifestPayload;
}

const worldEndpoint = (worldId: BuilderWorldId) => `/builder/worlds/${worldId}`;

export const craftMaterialListEndpoint = (worldId: BuilderWorldId) => (
  `${worldEndpoint(worldId)}/craftmaterials/`
);

export const craftMaterialDetailEndpoint = (
  worldId: BuilderWorldId,
  materialId: BuilderEntityId,
) => `${craftMaterialListEndpoint(worldId)}${materialId}/`;

export const craftingRecipeListEndpoint = (worldId: BuilderWorldId) => (
  `${worldEndpoint(worldId)}/craftingrecipes/`
);

export const craftingRecipeDetailEndpoint = (
  worldId: BuilderWorldId,
  recipeId: BuilderEntityId,
) => `${craftingRecipeListEndpoint(worldId)}${recipeId}/`;

export const craftingProfileListEndpoint = (worldId: BuilderWorldId) => (
  `${worldEndpoint(worldId)}/craftingprofiles/`
);

export const craftingProfileDetailEndpoint = (
  worldId: BuilderWorldId,
  profileId: BuilderEntityId,
) => `${craftingProfileListEndpoint(worldId)}${profileId}/`;

export const fetchCraftMaterial = async (
  worldId: BuilderWorldId,
  materialId: BuilderEntityId,
): Promise<CraftMaterialDetail> => {
  const response = await axios.get<CraftMaterialDetail>(
    craftMaterialDetailEndpoint(worldId, materialId),
  );
  return response.data;
};

export const fetchCraftingRecipe = async (
  worldId: BuilderWorldId,
  recipeId: BuilderEntityId,
): Promise<CraftingRecipeDetail> => {
  const response = await axios.get<CraftingRecipeDetail>(
    craftingRecipeDetailEndpoint(worldId, recipeId),
  );
  return response.data;
};

export const fetchCraftingProfile = async (
  worldId: BuilderWorldId,
  profileId: BuilderEntityId,
): Promise<CraftingProfileDetail> => {
  const response = await axios.get<CraftingProfileDetail>(
    craftingProfileDetailEndpoint(worldId, profileId),
  );
  return response.data;
};

export const applyCraftingManifest = async (
  worldId: BuilderWorldId,
  manifest: string,
): Promise<CraftingManifestApplyResponse> => {
  const response = await axios.post<CraftingManifestApplyResponse>(
    `${worldEndpoint(worldId)}/manifests/apply/`,
    { manifest },
  );
  return response.data;
};

export const craftingApiErrorMessage = (
  error: unknown,
  fallbackMessage: string,
): string => {
  if (!axios.isAxiosError(error)) return fallbackMessage;
  const data = error.response?.data;
  if (!data) return fallbackMessage;
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return String(data[0] || fallbackMessage);
  if (typeof data !== "object") return fallbackMessage;

  const responseData = data as Record<string, unknown>;
  if (typeof responseData.detail === "string") return responseData.detail;
  const firstValue = responseData[Object.keys(responseData)[0]];
  if (Array.isArray(firstValue)) return String(firstValue[0] || fallbackMessage);
  return typeof firstValue === "string" ? firstValue : fallbackMessage;
};
