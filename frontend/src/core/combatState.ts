export interface CombatMember {
  key: string;
  name: string;
  side: number;
  relation: "self" | "ally" | "enemy" | "neutral";
  health: number;
  health_max: number;
  current_target: string | null;
  effects: any[];
}

export interface CombatSnapshot {
  encounter_id: number;
  state_revision: number;
  round: number;
  status: "active" | "paused" | "finished";
  self: string | null;
  participants: CombatMember[];
  merged_into?: number | null;
  world_id?: number;
  room_id?: number;
}

export interface CombatState {
  current: CombatSnapshot | null;
  revisions: Record<number, number>;
  retired: Record<number, boolean>;
}

export const initialCombatState = (): CombatState => ({ current: null, revisions: {}, retired: {} });

export function applyCombatSnapshot(state: CombatState, snapshot: CombatSnapshot, viewer: string): CombatState {
  if (!snapshot || !Number.isSafeInteger(snapshot.encounter_id)
      || !Number.isSafeInteger(snapshot.state_revision) || !Array.isArray(snapshot.participants)) return state;
  const id = snapshot.encounter_id;
  if (state.retired[id] || snapshot.state_revision <= (state.revisions[id] ?? -1)) return state;
  const revisions = { ...state.revisions, [id]: snapshot.state_revision };
  const retired = { ...state.retired };
  let current = state.current;
  if (snapshot.status === "finished" || snapshot.merged_into) retired[id] = true;
  if (snapshot.self === viewer && snapshot.status !== "finished") {
    current = structuredClone(snapshot);
  } else if (current?.encounter_id === id) {
    current = null;
  }
  // Bound observer traffic retained in a room; keep the current encounter.
  const ids = Object.keys(revisions).map(Number);
  for (const old of ids.slice(0, Math.max(0, ids.length - 64))) {
    if (old !== current?.encounter_id) { delete revisions[old]; delete retired[old]; }
  }
  return { current, revisions, retired };
}

export function currentCombatTarget(state: CombatState): CombatMember | null {
  const snapshot = state.current;
  const own = snapshot?.participants.find(member => member.key === snapshot.self);
  return snapshot?.participants.find(member => member.key === own?.current_target) ?? null;
}
