import axios from "axios";

export type BuilderWorldId = string | number;
export type BuilderEntityId = string | number;
export type ManifestDocument = Record<string, unknown>;
export type ManifestResourceKind =
  | "world"
  | "craftmaterial"
  | "craftingrecipe"
  | "craftingprofile"
  | "merchantprofile"
  | "trainerprofile";
export type ManifestResourceResponseField =
  | "craft_material"
  | "crafting_recipe"
  | "crafting_profile"
  | "merchant_profile"
  | "trainer_profile";

export interface ManifestBackedDetail {
  manifest: ManifestDocument;
  yaml: string;
  delete_manifest: ManifestDocument;
  delete_yaml: string;
}

export interface ManifestApplyResponse {
  kind: string;
  operation: "created" | "updated" | "deleted" | "applied";
  [key: string]: unknown;
}

const worldEndpoint = (worldId: BuilderWorldId) => `/builder/worlds/${worldId}`;

export const applyWorldManifest = async <
  ResponsePayload extends ManifestApplyResponse = ManifestApplyResponse,
>(
  worldId: BuilderWorldId,
  manifest: string,
  expectedKind?: ManifestResourceKind,
): Promise<ResponsePayload> => {
  const payload: { manifest: string; expected_kind?: ManifestResourceKind } = { manifest };
  if (expectedKind) payload.expected_kind = expectedKind;
  const response = await axios.post<ResponsePayload>(
    `${worldEndpoint(worldId)}/manifests/apply/`,
    payload,
  );
  return response.data;
};

export const manifestApiErrorMessage = (
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
