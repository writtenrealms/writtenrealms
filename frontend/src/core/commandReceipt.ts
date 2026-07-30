export const COMMAND_RECEIPT_TIMEOUT_MS = 2_000;

export type CommandReceiptPhase =
  | "sending"
  | "received"
  | "accepted"
  | "unconfirmed"
  | "cancelled";

export type CommandReceiptSegmentPhase =
  | "accepted"
  | "completed"
  | "cancelled"
  | "rejected";

export interface CommandReceiptSegment {
  phase: CommandReceiptSegmentPhase;
  updated_at: number;
  message?: string;
}

export interface CommandReceipt {
  phase: CommandReceiptPhase;
  compact: boolean;
  updated_at: number;
  message?: string;
  segments: Record<string, CommandReceiptSegment>;
  terminal_failure?: boolean;
}

export interface CommandReceiptTransition {
  phase?: CommandReceiptPhase;
  compact?: boolean;
  expected_phase?: CommandReceiptPhase;
  message?: string;
  request_segment?: string;
  segment_status?: CommandReceiptSegmentPhase;
}

export interface CommandReceiptPresentation {
  text: string;
  ariaLabel: string;
  problem: boolean;
  state: "pending" | "success" | "error";
}

export interface CommandTerminalResult {
  requestId: string;
  requestSegment: string;
  segmentStatus: "completed" | "rejected";
  message?: string;
}

export interface CommandRequestSegments {
  requestId: string;
  requestSegments: string[];
}

export type CommandResolutionKind = "alias" | "history";

export interface CommandResolution {
  kind: CommandResolutionKind;
  originalText: string;
  requestId: string | null;
}

type CommandCrypto = Pick<Crypto, "getRandomValues"> & {
  randomUUID?: () => string;
};

/**
 * Generate an idempotency-safe UUID for a player command.
 *
 * randomUUID is not available in every browser context, so retain a
 * getRandomValues implementation rather than falling back to Math.random.
 */
export const createCommandRequestId = (
  cryptoSource: CommandCrypto | undefined = globalThis.crypto,
): string => {
  if (!cryptoSource?.getRandomValues) {
    throw new Error("Secure command identifiers are unavailable.");
  }
  if (cryptoSource.randomUUID) {
    return cryptoSource.randomUUID.call(cryptoSource);
  }

  const bytes = cryptoSource.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10).join(""),
  ].join("-");
};

export const initialCommandReceipt = (now = Date.now()): CommandReceipt => ({
  phase: "sending",
  compact: false,
  updated_at: now,
  segments: {},
});

const transitionCommandSegment = (
  current: CommandReceipt,
  transition: CommandReceiptTransition,
  now: number,
): CommandReceipt => {
  if (current.terminal_failure) return current;

  const segment = transition.request_segment?.trim() || "r";
  const nextSegmentPhase = transition.segment_status;
  if (!nextSegmentPhase) return current;
  const currentSegments = current.segments || {};
  const existingSegment = currentSegments[segment];
  const existingPhase = existingSegment?.phase;
  const existingFailed =
    existingPhase === "cancelled" || existingPhase === "rejected";
  const nextFailed =
    nextSegmentPhase === "cancelled" || nextSegmentPhase === "rejected";

  let nextSegment = existingSegment;
  if (!existingFailed) {
    if (nextFailed || !existingSegment) {
      nextSegment = {
        phase: nextSegmentPhase,
        updated_at: now,
        ...(transition.message ? { message: transition.message } : {}),
      };
    } else if (
      existingPhase === "accepted" &&
      nextSegmentPhase === "completed"
    ) {
      nextSegment = {
        phase: "completed",
        updated_at: now,
      };
    }
  }

  const segments = {
    ...currentSegments,
    ...(nextSegment ? { [segment]: nextSegment } : {}),
  };
  const segmentStates = Object.values(segments);
  const failedSegment = segmentStates.find(state => (
    state.phase === "cancelled" || state.phase === "rejected"
  ));
  const hasActiveSegment = segmentStates.some(
    state => state.phase === "accepted",
  );

  if (failedSegment) {
    return {
      ...current,
      phase: "cancelled",
      compact: false,
      updated_at: now,
      segments,
      message: transition.message || failedSegment.message || current.message,
    };
  }

  return {
    ...current,
    phase: "accepted",
    compact: !hasActiveSegment,
    updated_at: now,
    segments,
  };
};

/**
 * Apply lifecycle updates without allowing out-of-order WebSocket messages to
 * regress an accepted or cancelled command back to merely received.
 */
export const transitionCommandReceipt = (
  current: CommandReceipt,
  transition: CommandReceiptTransition,
  now = Date.now(),
): CommandReceipt => {
  if (transition.segment_status) {
    return transitionCommandSegment(current, transition, now);
  }
  if (
    transition.expected_phase &&
    current.phase !== transition.expected_phase
  ) {
    return current;
  }

  const nextPhase = transition.phase;
  if (
    nextPhase === "unconfirmed" &&
    (
      current.phase === "cancelled" ||
      (current.phase === "accepted" && current.compact)
    )
  ) {
    return current;
  }
  if (
    nextPhase === "received" &&
    (current.phase === "accepted" || current.phase === "cancelled")
  ) {
    return current;
  }
  if (nextPhase === "accepted" && current.phase === "cancelled") {
    return current;
  }
  if (nextPhase === "sending") {
    return current;
  }

  return {
    ...current,
    phase: nextPhase ?? current.phase,
    compact: transition.compact ?? (
      nextPhase && nextPhase !== current.phase ? false : current.compact
    ),
    updated_at: now,
    ...(transition.message ? { message: transition.message } : {}),
    ...(nextPhase === "cancelled" ? { terminal_failure: true } : {}),
  };
};

export const commandRequestId = (message: any): string | null => {
  const requestId = message?.data?.request_id ?? message?.request_id;
  return typeof requestId === "string" && requestId ? requestId : null;
};

/**
 * Return the authoritative terminal result for one correlated command
 * segment. Trigger lifecycle frames deliberately do not match; alias/history
 * resolution frames are redispatch metadata rather than the result of the
 * resolved command. `cmd.request.completed` is the private terminal result
 * used when a command has no actor-facing success output.
 */
export const commandTerminalResult = (
  message: any,
): CommandTerminalResult | null => {
  const requestId = commandRequestId(message);
  const rawSegment = message?.data?.request_segment;
  const requestSegment = typeof rawSegment === "string"
    ? rawSegment.trim()
    : "";
  const messageType = typeof message?.type === "string"
    ? message.type
    : "";
  if (messageType.startsWith("cmd.trigger.")) return null;

  const succeeded = (
    messageType === "cmd.request.completed" ||
    (
      messageType.startsWith("cmd.") &&
      messageType.endsWith(".success")
    )
  );
  const failed = (
    messageType.startsWith("cmd.") &&
    (
      messageType.endsWith(".error") ||
      messageType.endsWith(".cancelled")
    )
  );

  if (!requestId || !requestSegment || (!succeeded && !failed)) {
    return null;
  }

  if (succeeded) {
    return {
      requestId,
      requestSegment,
      segmentStatus: "completed",
    };
  }

  const error = message?.data?.error;
  const messageDetail = message?.data?.message;
  const text = message?.text;
  const safeMessage = typeof error === "string" && error.trim()
    ? error.trim()
    : (
      typeof messageDetail === "string" && messageDetail.trim()
        ? messageDetail.trim()
        : (typeof text === "string" && text.trim() ? text.trim() : undefined)
    );
  return {
    requestId,
    requestSegment,
    segmentStatus: "rejected",
    ...(safeMessage ? { message: safeMessage } : {}),
  };
};

/**
 * Parse the private command-chain plan sent before any of its segment results.
 * Pre-seeding every segment prevents the first fast result from settling the
 * root receipt while later chain segments are still running.
 */
export const commandRequestSegments = (
  message: any,
): CommandRequestSegments | null => {
  if (message?.type !== "cmd.request.segments") return null;

  const requestId = commandRequestId(message);
  const rawSegments = message?.data?.request_segments;
  if (!requestId || !Array.isArray(rawSegments)) return null;

  const requestSegments = Array.from(new Set(
    rawSegments
      .filter(segment => typeof segment === "string")
      .map(segment => segment.trim())
      .filter(Boolean),
  ));
  if (!requestSegments.length) return null;
  return { requestId, requestSegments };
};

export const commandResolution = (
  message: any,
): CommandResolution | null => {
  if (message?.type === "cmd.alias.resolve") {
    const originalText = message?.data?.command;
    if (typeof originalText !== "string" || !originalText) return null;
    return {
      kind: "alias",
      originalText,
      requestId: commandRequestId(message),
    };
  }
  if (message?.type === "cmd.history.replay") {
    const originalText = message?.data?.reference;
    if (typeof originalText !== "string" || !originalText) return null;
    return {
      kind: "history",
      originalText,
      requestId: commandRequestId(message),
    };
  }
  return null;
};

export const commandResolutionEchoIndex = (
  messages: any[],
  resolution: CommandResolution,
): number => {
  if (resolution.requestId) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (
        message.echo &&
        message.request_id === resolution.requestId
      ) {
        return index;
      }
    }
    return -1;
  }

  // Old workers may omit request identity on expansion echoes. Matching the
  // oldest still-pending exact input preserves submission order without ever
  // associating a different alias/history expression.
  const pendingIndex = messages.findIndex(message => (
    message.echo &&
    message.command_resolution?.kind === resolution.kind &&
    !message.command_resolution.resolved &&
    message.command_resolution.original_text === resolution.originalText
  ));
  return pendingIndex;
};

export const commandReceiptPresentation = (
  receipt: CommandReceipt | null | undefined,
): CommandReceiptPresentation | null => {
  if (!receipt) return null;

  if (receipt.phase === "sending") {
    return {
      text: "…",
      ariaLabel: "Sending command. The server has not acknowledged it yet.",
      problem: false,
      state: "pending",
    };
  }
  if (receipt.phase === "unconfirmed") {
    return {
      text: "×",
      ariaLabel: receipt.message
        ? `${receipt.message} The command was not retried automatically.`
        : (
          "Command delivery could not be confirmed. "
          + "The command was not retried automatically."
        ),
      problem: true,
      state: "error",
    };
  }
  if (receipt.phase === "cancelled") {
    return {
      text: "×",
      ariaLabel: receipt.message
        ? `Action failed. ${receipt.message}`
        : "Action failed.",
      problem: true,
      state: "error",
    };
  }
  if (receipt.compact) {
    return {
      text: "✓",
      ariaLabel: receipt.phase === "accepted"
        ? "Action completed successfully."
        : "Command received by the server.",
      problem: false,
      state: "success",
    };
  }
  if (receipt.phase === "accepted") {
    return {
      text: "…",
      ariaLabel: "Action is still underway.",
      problem: false,
      state: "pending",
    };
  }
  return {
    text: "…",
    ariaLabel: "Command received by the server and is being processed.",
    problem: false,
    state: "pending",
  };
};
