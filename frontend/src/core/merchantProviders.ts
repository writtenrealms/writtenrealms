export interface MerchantProvider {
  type?: string;
  id?: string | number | null;
  key?: string | null;
  ref?: string | null;
  name?: string | null;
}

const asProvider = (value: unknown): MerchantProvider | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const provider = value as MerchantProvider;
  if (
    provider.id === undefined
    && !String(provider.key || "").trim()
    && !String(provider.ref || "").trim()
    && !String(provider.name || "").trim()
  ) {
    return null;
  }
  return provider;
};

export const getRoomMerchantProvider = (room: any): MerchantProvider | null => {
  const provider = asProvider(room?.merchant_provider);
  if (provider) return provider;

  // Compatibility for state snapshots produced while the room-provider
  // payload is rolling out. This remains a structured signal and never
  // treats a custom `shop` room action as proof that a merchant exists.
  const profile = asProvider(room?.merchant_profile);
  if (!profile) return null;
  return {
    type: "room",
    id: room?.id ?? profile.id,
    key: room?.key || profile.key,
    name: profile.name || room?.name,
  };
};

export const merchantProviderTarget = (
  provider: MerchantProvider | null | undefined,
): string => {
  if (!provider) return "";
  return String(provider.key || provider.ref || provider.name || "").trim();
};

const providersMatch = (
  left: MerchantProvider | null | undefined,
  right: MerchantProvider | null | undefined,
): boolean => {
  if (!left || !right) return false;
  const leftType = String(left.type || "").trim().toLowerCase();
  const rightType = String(right.type || "").trim().toLowerCase();
  if (leftType && rightType && leftType !== rightType) return false;
  const leftTarget = merchantProviderTarget(left);
  const rightTarget = merchantProviderTarget(right);
  if (leftTarget && rightTarget) return leftTarget === rightTarget;
  return left.id != null && right.id != null && String(left.id) === String(right.id);
};

export const merchantProviderIsAvailableInRoom = (
  provider: MerchantProvider | null | undefined,
  room: any,
): boolean => {
  if (!provider || !room) return false;
  const providerType = String(provider.type || "").trim().toLowerCase();
  if (providerType === "room") {
    return providersMatch(provider, getRoomMerchantProvider(room));
  }
  if (providerType === "mob") {
    return (room.chars || []).some(
      (char: any) => char?.is_merchant && providersMatch(provider, {
        type: "mob",
        id: char.id,
        key: char.key,
        name: char.name,
      }),
    );
  }
  return (
    providersMatch(provider, getRoomMerchantProvider(room))
    || (room.chars || []).some(
      (char: any) => char?.is_merchant && providersMatch(provider, char),
    )
  );
};

export const roomHasMerchantProvider = (room: any): boolean => (
  getRoomMerchantProvider(room) !== null
);

export const roomActionsForMerchantProvider = (room: any): string[] => {
  const actions = Array.isArray(room?.actions) ? [...room.actions] : [];
  if (getRoomMerchantProvider(room)?.type !== "room") return actions;

  const normalizedActions = new Set<string>();
  const directMerchantActions: string[] = [];
  for (const rawAction of actions) {
    const action = String(rawAction || "").trim();
    const normalizedAction = action.toLowerCase();
    if (!action || normalizedAction === "shop" || normalizedActions.has(normalizedAction)) {
      continue;
    }
    normalizedActions.add(normalizedAction);
    directMerchantActions.push(action);
  }
  for (const action of ["list", "offer"]) {
    if (!normalizedActions.has(action)) {
      normalizedActions.add(action);
      directMerchantActions.push(action);
    }
  }
  return directMerchantActions;
};
