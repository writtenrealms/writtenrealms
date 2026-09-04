export type ActiveRoundEffect = {
  id?: number | string;
  effect?: string;
  label?: string;
  category?: string;
  scope?: string;
  stack_key?: string;
  remaining_rounds?: number | string;
  duration_rounds?: number | string;
  primitives?: Array<{
    type?: string;
    remaining?: number | string;
  }>;
  encounter_id?: number | string;
  source?: {
    type?: string;
    id?: number | string;
  };
};

export type RoundEffectPresentation = {
  key: string;
  label: string;
  category: string;
  remainingRounds: number;
  roundsLabel: string;
  barrierRemaining: number | null;
  fillWidth: string;
  title: string;
};

export type RoundEffectsByScope = {
  characterEffects: ActiveRoundEffect[];
  encounterEffects: ActiveRoundEffect[];
};

type CombatantEffectSnapshot = {
  target?: { key?: string };
  active_effects?: ActiveRoundEffect[];
};

const EFFECT_STATUS_LABELS: Record<string, string> = {
  stun: "Stunned",
};

const capfirst = (value: string): string => (
  value ? value.charAt(0).toUpperCase() + value.slice(1) : value
);

export const presentRoundEffects = (
  effects: ActiveRoundEffect[] | null | undefined,
  { useStatusLabels = false }: { useStatusLabels?: boolean } = {},
): RoundEffectPresentation[] => (
  (Array.isArray(effects) ? effects : [])
    .map((effect, index) => {
      const remainingRounds = Number(effect.remaining_rounds || 0);
      const durationRounds = Math.max(
        remainingRounds,
        Number(effect.duration_rounds || remainingRounds || 1),
      );
      const fillPercent = Math.min(
        100,
        Math.max(0, Math.round((remainingRounds / durationRounds) * 100)),
      );
      const effectCode = String(effect.effect || "").trim().toLowerCase();
      const authoredLabel = String(effect.label || effect.effect || "Effect");
      const label = useStatusLabels && EFFECT_STATUS_LABELS[effectCode]
        ? EFFECT_STATUS_LABELS[effectCode]
        : capfirst(authoredLabel);
      const source = effect.source || {};
      const key = String(effect.id ?? [
        effect.stack_key || effect.effect || label,
        effect.encounter_id || "character",
        source.type || "source",
        source.id || index,
        index,
      ].join(":"));
      const roundLabel = durationRounds === 1 ? "round" : "rounds";
      const barrierValues = (effect.primitives || [])
        .filter(primitive => primitive?.type === "damage_absorb")
        .map(primitive => Number(primitive.remaining))
        .filter(remaining => Number.isFinite(remaining) && remaining >= 0);
      const barrierRemaining = barrierValues.length
        ? barrierValues.reduce((total, remaining) => total + remaining, 0)
        : null;
      const barrierTitle = barrierRemaining === null
        ? ""
        : `; ${barrierRemaining} barrier remaining`;
      return {
        key,
        label,
        category: String(effect.category || "neutral").toLowerCase(),
        remainingRounds,
        roundsLabel: `${remainingRounds} ${remainingRounds === 1 ? "rd" : "rds"}`,
        barrierRemaining,
        fillWidth: `${fillPercent}%`,
        title: `${label}: ${remainingRounds} of ${durationRounds} ${roundLabel} remaining${barrierTitle}`,
      };
    })
    .filter(effect => effect.remainingRounds > 0)
);

export const splitRoundEffectsByScope = (
  effects: ActiveRoundEffect[] | null | undefined,
): RoundEffectsByScope => {
  const characterEffects: ActiveRoundEffect[] = [];
  const encounterEffects: ActiveRoundEffect[] = [];
  for (const effect of Array.isArray(effects) ? effects : []) {
    if (String(effect?.scope || "").toLowerCase() === "character") {
      characterEffects.push(effect);
    } else {
      encounterEffects.push(effect);
    }
  }
  return { characterEffects, encounterEffects };
};

export const playerRoundEffectSnapshot = (
  combatants: CombatantEffectSnapshot[] | null | undefined,
  playerKey: string | null | undefined,
): RoundEffectsByScope | null => {
  if (!playerKey || !Array.isArray(combatants)) return null;
  const playerSnapshot = combatants.find(
    combatant => combatant?.target?.key === playerKey,
  );
  if (!playerSnapshot || !Array.isArray(playerSnapshot.active_effects)) return null;
  return splitRoundEffectsByScope(playerSnapshot.active_effects);
};
