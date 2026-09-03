export type ActiveRoundEffect = {
  id?: number | string;
  effect?: string;
  label?: string;
  category?: string;
  stack_key?: string;
  remaining_rounds?: number | string;
  duration_rounds?: number | string;
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
  fillWidth: string;
  title: string;
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
      return {
        key,
        label,
        category: String(effect.category || "neutral").toLowerCase(),
        remainingRounds,
        fillWidth: `${fillPercent}%`,
        title: `${label}: ${remainingRounds} of ${durationRounds} ${roundLabel} remaining`,
      };
    })
    .filter(effect => effect.remainingRounds > 0)
);
