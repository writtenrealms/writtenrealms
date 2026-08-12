export interface TrainingProvider {
  type?: string;
  id?: string | number | null;
  key?: string | null;
  ref?: string | null;
  name?: string | null;
  profile?: {
    id?: string | number | null;
    key?: string | null;
    slug?: string | null;
    name?: string | null;
  } | null;
}

const asProvider = (value: unknown): TrainingProvider | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const provider = value as TrainingProvider;
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

export const getRoomTrainingProvider = (room: any): TrainingProvider | null => (
  asProvider(room?.training_provider)
);

const providerIdentity = (provider: TrainingProvider | null | undefined): string => (
  String(provider?.key || provider?.ref || "").trim()
);

const providersMatch = (
  left: TrainingProvider | null | undefined,
  right: TrainingProvider | null | undefined,
): boolean => {
  if (!left || !right) return false;
  const leftType = String(left.type || "").trim().toLowerCase();
  const rightType = String(right.type || "").trim().toLowerCase();
  if (leftType && rightType && leftType !== rightType) return false;

  const leftIdentity = providerIdentity(left);
  const rightIdentity = providerIdentity(right);
  if (leftIdentity && rightIdentity) return leftIdentity === rightIdentity;
  if (left.id != null && right.id != null) {
    return String(left.id) === String(right.id);
  }
  return Boolean(
    left.name
    && right.name
    && String(left.name).trim().toLowerCase() === String(right.name).trim().toLowerCase()
  );
};

const charTrainingProvider = (char: any): TrainingProvider => ({
  ...(asProvider(char?.training_provider) || {}),
  type: char?.training_provider?.type || "mob",
  id: char?.training_provider?.id ?? char?.id,
  key: char?.training_provider?.key || char?.key,
  name: char?.training_provider?.name || char?.name,
});

export const trainingProviderIsAvailableInRoom = (
  provider: TrainingProvider | null | undefined,
  room: any,
): boolean => {
  // Abilities without a trainer requirement are valid independently of the
  // room in which the list was opened.
  if (!provider) return true;
  if (!room) return false;

  const providerType = String(provider.type || "").trim().toLowerCase();
  if (providerType === "room") {
    return providersMatch(provider, getRoomTrainingProvider(room));
  }

  const matchingMobIsPresent = (room.chars || []).some((char: any) => (
    (char?.is_trainer || char?.training_provider)
    && providersMatch(provider, charTrainingProvider(char))
  ));
  if (providerType === "mob") return matchingMobIsPresent;
  return (
    providersMatch(provider, getRoomTrainingProvider(room))
    || matchingMobIsPresent
  );
};

export const roomActionsForTrainingProvider = (
  room: any,
  sourceActions?: string[],
): string[] => {
  const actions = Array.isArray(sourceActions)
    ? [...sourceActions]
    : Array.isArray(room?.actions) ? [...room.actions] : [];
  const provider = getRoomTrainingProvider(room);
  if (!provider) return actions;
  if (
    String(provider.type || "").trim().toLowerCase() === "mob"
    && !trainingProviderIsAvailableInRoom(provider, room)
  ) {
    return actions.filter(action => !["learn", "unlearn"].includes(
      String(action || "").trim().toLowerCase(),
    ));
  }

  const normalized = new Set<string>();
  const directTrainingActions: string[] = [];
  for (const rawAction of actions) {
    const action = String(rawAction || "").trim();
    const normalizedAction = action.toLowerCase();
    if (!action || normalized.has(normalizedAction)) continue;
    normalized.add(normalizedAction);
    directTrainingActions.push(action);
  }
  for (const action of ["learn", "unlearn"]) {
    if (!normalized.has(action)) {
      normalized.add(action);
      directTrainingActions.push(action);
    }
  }
  return directTrainingActions;
};
