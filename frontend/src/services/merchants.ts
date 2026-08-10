import axios from "axios";
import type {
  BuilderEntityId,
  BuilderWorldId,
  ManifestBackedDetail,
} from "@/services/manifests";

export type MerchantFundsMode = "unlimited" | "finite";

export interface MerchantProfileSummary {
  id: number;
  key: string;
  slug: string;
  name: string;
  model_type: string;
  modified_ts: string;
  notes: string;
  sell_markup: number;
  buy_multiplier: number;
  restock_interval_seconds: number | null;
  funds_mode: MerchantFundsMode;
  settlement_currency: string;
  purchase_budget: number;
  buyback_enabled: boolean;
  buyback_max_items: number;
  stock_count: number;
}

export interface MerchantStockSlot {
  key: string;
  count: number;
  refresh: "fill_missing" | "reroll_on_restock";
  item_definition?: string;
  item_bundle?: string;
}

export interface MerchantProfileDetail
  extends MerchantProfileSummary,
    ManifestBackedDetail {
  buyback_expires: "on_restock";
  stock: MerchantStockSlot[];
}

const worldEndpoint = (worldId: BuilderWorldId) => `/builder/worlds/${worldId}`;

export const merchantProfileListEndpoint = (worldId: BuilderWorldId) => (
  `${worldEndpoint(worldId)}/merchantprofiles/`
);

export const merchantProfileDetailEndpoint = (
  worldId: BuilderWorldId,
  profileId: BuilderEntityId,
) => `${merchantProfileListEndpoint(worldId)}${profileId}/`;

export const fetchMerchantProfile = async (
  worldId: BuilderWorldId,
  profileId: BuilderEntityId,
): Promise<MerchantProfileDetail> => {
  const response = await axios.get<MerchantProfileDetail>(
    merchantProfileDetailEndpoint(worldId, profileId),
  );
  return response.data;
};
