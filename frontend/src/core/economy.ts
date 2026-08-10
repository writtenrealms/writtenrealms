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

const replaceCurrencyLabel = (display: string, label: string): string => {
  if (!label) return display;
  const normalizedDisplay = display.toLocaleLowerCase();
  const normalizedLabel = label.toLocaleLowerCase();
  const isTokenCharacter = (value: string) => /[\p{L}\p{N}_]/u.test(value);
  let searchFrom = normalizedDisplay.length;

  while (searchFrom >= 0) {
    const start = normalizedDisplay.lastIndexOf(normalizedLabel, searchFrom);
    if (start === -1) return display;
    const end = start + label.length;
    const hasLeftBoundary = (
      !isTokenCharacter(label[0])
      || !isTokenCharacter(display[start - 1] || "")
    );
    const hasRightBoundary = (
      !isTokenCharacter(label[label.length - 1])
      || !isTokenCharacter(display[end] || "")
    );
    if (hasLeftBoundary && hasRightBoundary) {
      return [
        display.slice(0, start),
        display.slice(start, end).toUpperCase(),
        display.slice(end),
      ].join("");
    }
    searchFrom = start - 1;
  }

  return display;
};

export const formatMoneyUppercaseCurrency = (
  money: Money,
  economy?: EconomyCatalog | null,
): string => {
  const code = String(money.currency || "").trim();
  const definition = economy?.currencies?.[code];
  const label = displayName(definition, code, money.amount);
  const authoredDisplay = money.display?.trim();
  if (authoredDisplay) {
    const labels = [
      definition?.plural_name?.trim(),
      definition?.name?.trim(),
      code,
    ]
      .filter((candidate): candidate is string => Boolean(candidate))
      .sort((left, right) => right.length - left.length);
    for (const candidate of labels) {
      const updatedDisplay = replaceCurrencyLabel(authoredDisplay, candidate);
      if (updatedDisplay !== authoredDisplay) return updatedDisplay;
    }
    return authoredDisplay;
  }

  return label ? `${money.amount} ${label.toUpperCase()}` : String(money.amount);
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
