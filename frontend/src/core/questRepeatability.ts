import { gameplayTimeMs, type InstanceTimeControl } from "./instanceTimeControl";

export interface QuestRepeatability {
  mode: "never" | "always" | "cooldown" | "daily";
  cooldown_seconds: number;
  state: "waiting" | "ready" | "unavailable";
  ready_at: string | null;
  remaining_seconds: number | null;
  template_status: string;
  reset_at?: string;
  timezone?: string;
}

export interface QuestClock {
  control: InstanceTimeControl | null;
  nowMs: number;
  fetchedAtMs: number;
  gameplayAtFetchMs: number;
  serverTimeAtFetchMs: number | null;
  wallTimeAtFetchMs: number | null;
  offsetAtFetchMs: number;
}

export const questRemainingSeconds = (repeatability: QuestRepeatability, clock: QuestClock): number => {
  if (repeatability.state !== "waiting") return 0;
  const daily = repeatability.mode === "daily";
  const elapsedMs = Math.max(0, daily
    ? clock.nowMs - clock.fetchedAtMs
    : gameplayTimeMs(clock.control, clock.nowMs) - clock.gameplayAtFetchMs);
  const anchor = daily ? clock.wallTimeAtFetchMs : clock.serverTimeAtFetchMs;
  const readyAt = Date.parse(repeatability.ready_at || "");
  if (anchor !== null && Number.isFinite(readyAt)) {
    return Math.max(0, Math.ceil((readyAt - anchor - elapsedMs) / 1000));
  }
  return Math.max(0, Number(repeatability.remaining_seconds || 0) - Math.floor(elapsedMs / 1000));
};

export const questReadyAtTitle = (repeatability: QuestRepeatability, clock: QuestClock): string => {
  if (repeatability.state !== "waiting") return "";
  const daily = repeatability.mode === "daily";
  if (!daily && clock.control?.paused) return "This cooldown advances with instance time.";
  const offsetChange = daily ? 0 : (clock.control?.clock_offset_seconds || 0) * 1000 - clock.offsetAtFetchMs;
  const readyAt = new Date(Date.parse(repeatability.ready_at || "") + offsetChange);
  if (!Number.isFinite(readyAt.getTime())) return "";
  if (daily && repeatability.timezone) {
    try {
      return `Daily reset at ${readyAt.toLocaleString(undefined, { timeZone: repeatability.timezone })} (${repeatability.timezone})`;
    } catch (error) {
      // Browser timezone databases can differ from the server's ZoneInfo database.
      if (!(error instanceof RangeError)) throw error;
    }
  }
  return `Ready at ${readyAt.toLocaleString()}`;
};
