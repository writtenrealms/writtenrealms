export interface CurrencyDefinition {
  name: string;
  plural_name?: string;
  description?: string;
}

export interface CurrencyReference extends CurrencyDefinition {
  code: string;
}

export interface EconomyCatalog {
  revision: number;
  default_currency: string | null;
  currencies: Record<string, CurrencyDefinition>;
}

export interface PlayerEconomy {
  wallet_revision: number;
  balances: Record<string, number>;
}

export interface Money {
  amount: number;
  currency: string;
  display?: string;
}

export interface CurrencyBalanceChange {
  currency: CurrencyReference;
  delta: number;
  before: number;
  after: number;
  money: Money;
}

export interface CurrencyBalancesChangedData {
  player: number | string | { id?: number; key?: string };
  wallet_revision: number;
  reason: string;
  changes: CurrencyBalanceChange[];
}

export interface WalletBalanceEntry extends Money {
  label: string;
  display: string;
}

const displayName = (
  definition: CurrencyDefinition | undefined,
  code: string,
  amount: number,
): string => {
  if (!definition) return code;
  if (amount === 1) return definition.name;
  return definition.plural_name?.trim() || definition.name;
};

export const formatMoney = (
  money: Money,
  economy?: EconomyCatalog | null,
): string => {
  const authoredDisplay = money.display?.trim();
  if (authoredDisplay) return authoredDisplay;

  const code = String(money.currency || "").trim();
  const definition = economy?.currencies?.[code];
  const label = displayName(definition, code, money.amount);
  return label ? `${money.amount} ${label}` : String(money.amount);
};

export const walletBalanceEntries = (
  economy?: EconomyCatalog | null,
  wallet?: PlayerEconomy | null,
): WalletBalanceEntry[] => {
  if (!economy || !wallet) return [];

  const defaultCode = String(economy.default_currency || "").trim();
  const codes = [
    defaultCode,
    ...Object.keys(economy.currencies || {}),
    ...Object.keys(wallet.balances || {}),
  ].filter(Boolean);
  const uniqueCodes = [...new Set(codes)];

  return uniqueCodes
    .map((code) => {
      const amount = wallet.balances?.[code] ?? 0;
      const definition = economy.currencies?.[code];
      const money: Money = { amount, currency: code };
      return {
        ...money,
        label: displayName(definition, code, amount),
        display: formatMoney(money, economy),
      };
    })
    .filter((entry) => entry.currency === defaultCode || entry.amount > 0);
};
