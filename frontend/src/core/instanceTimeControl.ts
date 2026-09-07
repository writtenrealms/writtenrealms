export interface InstanceTimeControl {
  enabled: boolean;
  pause_in_combat: boolean;
  paused: boolean;
  clock_offset_seconds: number;
  tick: number;
  generation: number;
  pending_revision: number;
  simulation_time: string;
  pending_command: { command_type: string; payload: unknown; label: string } | null;
  can_advance: boolean;
  run_id: number;
  world_id?: number;
}

// A delayed event must never restore an earlier turn, pace, or prepared action.
export const applyInstanceTimeControl = (
  current: InstanceTimeControl | null,
  incoming: InstanceTimeControl | null,
  runtimeWorldId?: number,
): InstanceTimeControl | null => {
  if (incoming?.world_id && runtimeWorldId && incoming.world_id !== runtimeWorldId) return current;
  if (!incoming?.enabled) return null;
  if (current?.run_id === incoming.run_id) {
    if (incoming.generation < current.generation || incoming.tick < current.tick) return current;
    if (incoming.pending_revision < current.pending_revision) return current;
  }
  return incoming;
};

export const advanceTurnCommand = (control: InstanceTimeControl) => ({
  type: "cmd.advance",
  text: "advance",
  data: {
    run_id: control.run_id,
    expected_tick: control.tick,
    expected_generation: control.generation,
    expected_pending_revision: control.pending_revision,
  },
});

export const combatPauseCommand = (control: InstanceTimeControl, pause: boolean) => ({
  type: "cmd.time_control", text: pause ? "pause" : "resume", data: {
    pause_in_combat: pause,
    run_id: control.run_id,
    expected_generation: control.generation,
  },
});

export const simulationTimeMs = (control: InstanceTimeControl | null): number | null => {
  if (!control?.enabled || !control.paused) return null;
  const time = Date.parse(control.simulation_time);
  return Number.isFinite(time) ? time : null;
};

// A continuous clock for elapsed UI durations across any number of toggles.
export const gameplayTimeMs = (control: InstanceTimeControl | null, wallNow = Date.now()): number =>
  (simulationTimeMs(control) ?? wallNow) - (control?.clock_offset_seconds || 0) * 1000;

export const pendingTurnCommandState = (messages: Array<any>): "awaiting" | "uncertain" | null => {
  let pending = false;
  for (const message of messages) {
    const receipt = message.echo && message.command_receipt;
    if (!receipt) continue;
    if (receipt.phase === "unconfirmed") return "uncertain";
    if (receipt.phase === "sending" || receipt.phase === "received" || (receipt.phase === "accepted" && !receipt.compact)) pending = true;
  }
  return pending ? "awaiting" : null;
};
