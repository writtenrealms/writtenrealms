import axios from "axios";

export type BuilderWorldId = string | number;
export type BuilderCurrencyId = string | number;

export interface CurrencyUsage {
  type: string;
  count: number;
}

export interface BuilderCurrency {
  id: number;
  code: string;
  name: string;
  plural_name: string;
  description: string;
  is_default: boolean;
  starting_amount: number;
  usage: CurrencyUsage[];
  can_delete: boolean;
}

export interface CurrencyCreatePayload {
  code: string;
  name: string;
  plural_name: string;
  description: string;
  starting_amount: number;
}

export type CurrencyUpdatePayload = CurrencyCreatePayload;

interface PaginatedCurrencyResponse {
  results: BuilderCurrency[];
}

const currencyListEndpoint = (worldId: BuilderWorldId) => (
  `/builder/worlds/${worldId}/currencies/`
);

const currencyDetailEndpoint = (
  worldId: BuilderWorldId,
  currencyId: BuilderCurrencyId,
) => `${currencyListEndpoint(worldId)}${currencyId}/`;

export const fetchCurrencies = async (
  worldId: BuilderWorldId,
): Promise<BuilderCurrency[]> => {
  const response = await axios.get<PaginatedCurrencyResponse | BuilderCurrency[]>(
    currencyListEndpoint(worldId),
    { params: { page_size: 100 } },
  );
  const payload = response.data;
  return Array.isArray(payload) ? payload : payload.results || [];
};

export const createCurrency = async (
  worldId: BuilderWorldId,
  payload: CurrencyCreatePayload,
): Promise<BuilderCurrency> => {
  const response = await axios.post<BuilderCurrency>(currencyListEndpoint(worldId), payload);
  return response.data;
};

export const updateCurrency = async (
  worldId: BuilderWorldId,
  currencyId: BuilderCurrencyId,
  payload: CurrencyUpdatePayload,
): Promise<BuilderCurrency> => {
  const response = await axios.put<BuilderCurrency>(
    currencyDetailEndpoint(worldId, currencyId),
    payload,
  );
  return response.data;
};

export const deleteCurrency = async (
  worldId: BuilderWorldId,
  currencyId: BuilderCurrencyId,
): Promise<void> => {
  await axios.delete(currencyDetailEndpoint(worldId, currencyId));
};

export const makeDefaultCurrency = async (
  worldId: BuilderWorldId,
  currencyId: BuilderCurrencyId,
): Promise<BuilderCurrency> => {
  const response = await axios.post<BuilderCurrency>(
    `${currencyDetailEndpoint(worldId, currencyId)}make-default/`,
  );
  return response.data;
};

export const currencyApiErrorMessage = (
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
