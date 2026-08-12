export type TrainerLearningStatusName =
  | "unrestricted"
  | "available"
  | "limit_reached"
  | "denied";

export interface TrainerLearningStatus {
  profileId: string | number | null;
  profileKey: string;
  profileSlug: string;
  profileName: string;
  status: TrainerLearningStatusName;
  eligible: boolean;
  maxKnown: number | null;
  known: number;
  remaining: number | null;
  reason: string;
}

type UnknownRecord = Record<string, unknown>;

const asRecord = (value: unknown): UnknownRecord | null => (
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : null
);

const nonNegativeNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
};

const normalizedStatus = (value: unknown): string => (
  String(value || "").trim().toLowerCase().replace(/-/g, "_")
);

const providerProfile = (provider: unknown): UnknownRecord => {
  const record = asRecord(provider) || {};
  return asRecord(record.profile) || record;
};

export const normalizeTrainerLearningStatus = (
  value: unknown,
  provider?: unknown,
): TrainerLearningStatus | null => {
  const raw = asRecord(value);
  if (!raw) return null;

  const profile = asRecord(raw.profile) || providerProfile(provider);
  const maxKnownValue = raw.max_known ?? raw.maxKnown;
  const isUncapped = maxKnownValue === null
    || String(maxKnownValue || "").trim().toLowerCase() === "uncapped";
  const maxKnown = isUncapped ? null : nonNegativeNumber(maxKnownValue);
  const known = nonNegativeNumber(raw.known) ?? 0;
  const authoredRemaining = nonNegativeNumber(raw.remaining);
  const remaining = maxKnown === null
    ? null
    : authoredRemaining ?? Math.max(maxKnown - known, 0);
  const rawStatus = normalizedStatus(raw.status);
  const explicitlyDenied = rawStatus === "denied";
  const limitReached = rawStatus === "limit_reached"
    || (maxKnown !== null && (remaining === 0 || known >= maxKnown));
  let status: TrainerLearningStatusName;
  if (explicitlyDenied) status = "denied";
  else if (limitReached) status = "limit_reached";
  else if (rawStatus === "available" || rawStatus === "unrestricted") {
    status = rawStatus;
  } else status = maxKnown === null ? "unrestricted" : "available";
  const eligible = typeof raw.eligible === "boolean"
    ? raw.eligible
    : status === "available" || status === "unrestricted";

  return {
    profileId: (raw.profile_id ?? raw.profileId ?? profile.id ?? null) as string | number | null,
    profileKey: String(raw.profile_key ?? raw.profileKey ?? profile.key ?? "").trim(),
    profileSlug: String(raw.profile_slug ?? raw.profileSlug ?? profile.slug ?? "").trim(),
    profileName: String(raw.profile_name ?? raw.profileName ?? profile.name ?? "").trim(),
    status,
    eligible,
    maxKnown,
    known,
    remaining,
    reason: String(raw.reason ?? raw.message ?? "").trim(),
  };
};

const statusIdentity = (status: TrainerLearningStatus): string[] => [
  status.profileId === null ? "" : `id:${status.profileId}`,
  status.profileKey ? `key:${status.profileKey.toLowerCase()}` : "",
  status.profileSlug ? `slug:${status.profileSlug.toLowerCase()}` : "",
].filter(Boolean);

const providerIdentity = (provider: unknown): string[] => {
  const profile = providerProfile(provider);
  return [
    profile.id === null || profile.id === undefined ? "" : `id:${profile.id}`,
    profile.key ? `key:${String(profile.key).toLowerCase()}` : "",
    profile.slug ? `slug:${String(profile.slug).toLowerCase()}` : "",
  ].filter(Boolean);
};

export const trainerLearningStatusKey = (status: TrainerLearningStatus): string => (
  statusIdentity(status)[0]
  || (status.profileName ? `name:${status.profileName.toLowerCase()}` : "learning")
);

const topLevelLearningValues = (data: unknown): unknown[] => {
  const record = asRecord(data) || {};
  const values: unknown[] = [];
  for (const candidate of [record.learning, record.training_limits, record.trainingLimits]) {
    if (Array.isArray(candidate)) values.push(...candidate);
    else if (asRecord(candidate)) values.push(candidate);
  }
  return values;
};

const localLearningStatus = (ability: unknown): TrainerLearningStatus | null => {
  const record = asRecord(ability) || {};
  const trainer = asRecord(record.trainer)
    || asRecord(record.provider)
    || asRecord(record.training_provider);
  return normalizeTrainerLearningStatus(record.learning, trainer)
    || normalizeTrainerLearningStatus(trainer?.learning, trainer);
};

export const trainerLearningStatusForAbility = (
  ability: unknown,
  data: unknown,
): TrainerLearningStatus | null => {
  const local = localLearningStatus(ability);
  if (local) return local;

  const record = asRecord(ability) || {};
  const trainer = asRecord(record.trainer)
    || asRecord(record.provider)
    || asRecord(record.training_provider);
  if (!trainer) return null;
  const identities = new Set(providerIdentity(trainer));
  if (!identities.size) return null;

  for (const value of topLevelLearningValues(data)) {
    const status = normalizeTrainerLearningStatus(value);
    if (status && statusIdentity(status).some((identity) => identities.has(identity))) {
      return status;
    }
  }
  return null;
};

export const trainerLearningStatusesForMessage = (
  data: unknown,
  abilities: unknown[] = [],
): TrainerLearningStatus[] => {
  const record = asRecord(data) || {};
  const trainer = asRecord(record.trainer);
  const statuses = topLevelLearningValues(data)
    .map((value) => normalizeTrainerLearningStatus(
      value,
      value === record.learning ? trainer : undefined,
    ))
    .filter((value): value is TrainerLearningStatus => value !== null);

  const errorStatus = normalizeTrainerLearningStatus(trainer?.learning, trainer);
  if (errorStatus) statuses.push(errorStatus);

  for (const ability of abilities) {
    const status = localLearningStatus(ability);
    if (status) statuses.push(status);
  }

  const deduplicated = new Map<string, TrainerLearningStatus>();
  for (const status of statuses) {
    const key = trainerLearningStatusKey(status);
    if (!deduplicated.has(key)) deduplicated.set(key, status);
  }
  return [...deduplicated.values()];
};

export const trainerLearningLimitReached = (
  status: TrainerLearningStatus | null,
): boolean => Boolean(status && (
  status.status === "limit_reached"
  || (status.maxKnown !== null && status.remaining === 0)
));

export const trainerLearningChoiceIsAvailable = (
  status: TrainerLearningStatus | null,
): boolean => !status || (
  status.eligible
  && status.status !== "denied"
  && !trainerLearningLimitReached(status)
);

export const trainerLearningStatusText = (
  status: TrainerLearningStatus,
  options: { unlearn?: boolean } = {},
): string => {
  const name = status.profileName || status.profileSlug || "Training";
  if (status.status === "denied") {
    if (options.unlearn) {
      return `${name} — Learning is unavailable, but you can still unlearn known abilities here.`;
    }
    return `${name} — ${status.reason || "This training is not available to you."}`;
  }
  if (status.maxKnown === null) {
    return `${name} — No selection limit.`;
  }
  const summary = `${name} — ${status.known} of ${status.maxKnown} selected.`;
  return trainerLearningLimitReached(status)
    ? `${summary} Unlearn one to choose another.`
    : summary;
};
