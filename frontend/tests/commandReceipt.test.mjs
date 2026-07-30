import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(
  new URL("../src/core/commandReceipt.ts", import.meta.url),
  "utf8",
);
const messageComponentSource = await readFile(
  new URL(
    "../src/components/game/console/Message.vue",
    import.meta.url,
  ),
  "utf8",
);
const gameStoreSource = await readFile(
  new URL("../src/store/modules/game.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const receipt = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("secure fallback generates a version 4 UUID", () => {
  const bytes = Uint8Array.from({ length: 16 }, (_, index) => index);
  const requestId = receipt.createCommandRequestId({
    getRandomValues(target) {
      target.set(bytes);
      return target;
    },
  });

  assert.equal(requestId, "00010203-0405-4607-8809-0a0b0c0d0e0f");
});

test("receipt failure disclosure is teleported out of row layout", () => {
  assert.doesNotMatch(messageComponentSource, /<VTooltip/);
  assert.doesNotMatch(messageComponentSource, /Tooltip as VTooltip/);
  assert.doesNotMatch(messageComponentSource, /v-tooltip/);
  assert.doesNotMatch(messageComponentSource, /failureTooltipOptions/);
  assert.doesNotMatch(messageComponentSource, /document\.addEventListener/);
  assert.doesNotMatch(messageComponentSource, /ref="failureDetail/);
  assert.match(
    messageComponentSource,
    /document\.getElementById\(failureDetailId\.value\)/,
  );
  assert.match(messageComponentSource, /<Teleport v-if="failureDetailVisible"/);
  assert.match(messageComponentSource, /class="command-receipt-detail"/);
  assert.match(
    messageComponentSource,
    /\.command-receipt-detail\s*\{[^}]*position:\s*fixed/s,
  );
  assert.match(
    messageComponentSource,
    /v-else-if="receiptPresentation\.state === 'success'"/,
  );
  assert.match(messageComponentSource, /@pointerenter="showFailureDetailOnHover"/);
  assert.match(messageComponentSource, /@focus="showFailureDetailOnFocus"/);
  assert.match(messageComponentSource, /@click\.stop=/);
  assert.match(messageComponentSource, /@keydown\.esc/);
  assert.match(messageComponentSource, /min-width:\s*1\.5rem/);
  assert.match(messageComponentSource, /min-height:\s*1\.5rem/);
});

test("visible Trigger refusal text falls through to transcript handling", () => {
  const branchStart = gameStoreSource.indexOf(
    "if (message_data.type === TRIGGER_REJECTED_MESSAGE)",
  );
  const nextBranch = gameStoreSource.indexOf("} else if (", branchStart);
  const transcriptAdd = gameStoreSource.indexOf(
    'commit("message_add", message_data)',
    nextBranch,
  );
  const rejectionBranch = gameStoreSource.slice(branchStart, nextBranch);

  assert.ok(branchStart !== -1);
  assert.ok(nextBranch > branchStart);
  assert.match(rejectionBranch, /commandTriggerRejectionResult/);
  assert.match(
    rejectionBranch,
    /if \(!message_data\.text\)\s*\{\s*return;\s*\}/,
  );
  assert.ok(transcriptAdd > nextBranch);
});

test("Trigger cancellation uses the shared terminal receipt contract", () => {
  const branchStart = gameStoreSource.indexOf(
    "if (message_data.type === TRIGGER_CANCELLED_MESSAGE)",
  );
  const nextBranch = gameStoreSource.indexOf(
    "if (message_data.type === TRIGGER_REJECTED_MESSAGE)",
    branchStart,
  );
  const cancellationBranch = gameStoreSource.slice(branchStart, nextBranch);

  assert.ok(branchStart !== -1);
  assert.ok(nextBranch > branchStart);
  assert.match(cancellationBranch, /commandTerminalResult\(message_data\)/);
  assert.doesNotMatch(cancellationBranch, /segment_status:\s*"cancelled"/);
});

test("accepted does not regress when the gateway receipt arrives late", () => {
  const sending = receipt.initialCommandReceipt(1);
  const accepted = receipt.transitionCommandReceipt(
    sending,
    { phase: "accepted" },
    2,
  );
  const lateQueued = receipt.transitionCommandReceipt(
    accepted,
    { phase: "received" },
    3,
  );

  assert.deepEqual(lateQueued, accepted);
  assert.equal(
    receipt.commandReceiptPresentation(lateQueued).text,
    "…",
  );
});

test("later receipt can resolve provisional delivery uncertainty", () => {
  const unconfirmed = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    { phase: "unconfirmed" },
    2,
  );
  const received = receipt.transitionCommandReceipt(
    unconfirmed,
    { phase: "received" },
    3,
  );

  assert.equal(received.phase, "received");
  assert.equal(receipt.commandReceiptPresentation(received).text, "…");
});

test("queued receipt remains pending until a correlated terminal result", () => {
  const received = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    { phase: "received" },
    2,
  );

  assert.equal(received.phase, "received");
  assert.equal(received.compact, false);
  assert.equal(receipt.commandReceiptPresentation(received).text, "…");
});

test("correlated command success completes its request segment", () => {
  const result = receipt.commandTerminalResult({
    type: "cmd.look.success",
    text: "A Pier by the River Acheron",
    data: {
      request_id: "request-1",
      request_segment: "r.0",
    },
  });

  assert.deepEqual(result, {
    requestId: "request-1",
    requestSegment: "r.0",
    segmentStatus: "completed",
  });

  const completed = receipt.transitionCommandReceipt(
    receipt.transitionCommandReceipt(
      receipt.initialCommandReceipt(1),
      { phase: "received" },
      2,
    ),
    {
      request_segment: result.requestSegment,
      segment_status: result.segmentStatus,
    },
    3,
  );
  assert.equal(receipt.commandReceiptPresentation(completed).text, "✓");
});

test("private completion control completes a silent command segment", () => {
  assert.deepEqual(
    receipt.commandTerminalResult({
      type: "cmd.request.completed",
      data: {
        request_id: "request-1",
        request_segment: "r",
      },
    }),
    {
      requestId: "request-1",
      requestSegment: "r",
      segmentStatus: "completed",
    },
  );
});

test("correlated move refusal completes its request segment", () => {
  const result = receipt.commandTerminalResult({
    type: "cmd.move.error",
    text: "You cannot go that way.",
    data: {
      request_id: "request-1",
      request_segment: "r",
      error: "You cannot go that way.",
    },
  });

  assert.deepEqual(result, {
    requestId: "request-1",
    requestSegment: "r",
    segmentStatus: "completed",
  });

  const completed = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    {
      request_segment: result.requestSegment,
      segment_status: result.segmentStatus,
    },
    2,
  );
  assert.equal(receipt.commandReceiptPresentation(completed).text, "✓");
  assert.equal(
    receipt.commandReceiptPresentation(completed).failureDetail,
    undefined,
  );
});

test("correlated command cancellation completes its request segment", () => {
  assert.deepEqual(
    receipt.commandTerminalResult({
      type: "cmd.door.cancelled",
      data: {
        request_id: "request-1",
        request_segment: "r",
        message: "The door action was interrupted.",
      },
    }),
    {
      requestId: "request-1",
      requestSegment: "r",
      segmentStatus: "completed",
    },
  );
});

test("explicit completed status overrides an error-shaped response", () => {
  const messages = [
    {
      type: "cmd.buy.error",
      text: "The merchant does not sell that.",
      data: {
        request_id: "request-1",
        request_segment: "r",
        receipt_status: "completed",
      },
    },
    {
      type: "cmd.door.cancelled",
      text: "That door is already closed.",
      data: {
        request_id: "request-1",
        request_segment: "r",
        receipt_status: "completed",
      },
    },
    {
      type: "domain.authoritative.response",
      text: "Nothing answers.",
      data: {
        request_id: "request-1",
        request_segment: "r",
        receipt_status: "completed",
      },
    },
  ];

  for (const message of messages) {
    assert.deepEqual(receipt.commandTerminalResult(message), {
      requestId: "request-1",
      requestSegment: "r",
      segmentStatus: "completed",
    });
  }
});

test("explicit failed status overrides a success-shaped response", () => {
  assert.deepEqual(
    receipt.commandTerminalResult({
      type: "cmd.move.success",
      text: "The movement transaction failed.",
      data: {
        request_id: "request-1",
        request_segment: "r.3",
        receipt_status: "failed",
        error: "The movement transaction failed.",
      },
    }),
    {
      requestId: "request-1",
      requestSegment: "r.3",
      segmentStatus: "rejected",
      message: "The movement transaction failed.",
    },
  );
});

test("unsupported explicit receipt status does not fall back to suffix inference", () => {
  assert.equal(
    receipt.commandTerminalResult({
      type: "cmd.look.error",
      data: {
        request_id: "request-1",
        request_segment: "r",
        receipt_status: "pending",
      },
    }),
    null,
  );
});

test("legacy terminal suffix inference remains available without explicit status", () => {
  const messages = [
    {
      type: "cmd.look.success",
      data: { request_id: "request-1", request_segment: "r" },
    },
    {
      type: "cmd.text.error",
      text: "What?",
      data: { request_id: "request-1", request_segment: "r" },
    },
    {
      type: "cmd.craft.error",
      text: "You lack the materials.",
      data: { request_id: "request-1", request_segment: "r" },
    },
    {
      type: "cmd.close.cancelled",
      data: {
        request_id: "request-1",
        request_segment: "r",
        message: "The door action was interrupted.",
      },
    },
  ];

  for (const message of messages) {
    assert.deepEqual(receipt.commandTerminalResult(message), {
      requestId: "request-1",
      requestSegment: "r",
      segmentStatus: "completed",
    });
  }
});

test("expected Trigger refusal completes its receipt while retaining its prose", () => {
  const message = {
    type: "cmd.trigger.rejected",
    text: "Charon requires 10 obols.",
    data: {
      request_id: "request-1",
      request_segment: "r.4",
      status: "rejected",
      reason_code: "conditions_failed",
      receipt_status: "completed",
      message: "Charon requires 10 obols.",
    },
  };
  const result = receipt.commandTriggerRejectionResult(message);

  assert.deepEqual(result, {
    requestId: "request-1",
    requestSegment: "r.4",
    segmentStatus: "completed",
  });
  assert.equal(message.text, "Charon requires 10 obols.");

  const completed = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    {
      request_segment: result.requestSegment,
      segment_status: result.segmentStatus,
    },
    2,
  );
  const presentation = receipt.commandReceiptPresentation(completed);
  assert.equal(presentation.text, "✓");
  assert.equal(presentation.failureDetail, undefined);
});

test("legacy Trigger refusal completes as authoritative output", () => {
  const result = receipt.commandTriggerRejectionResult({
    type: "cmd.trigger.rejected",
    text: "The Trigger command could not be dispatched.",
    data: {
      request_id: "request-1",
      request_segment: "r.2",
      status: "rejected",
      reason_code: "trigger_failed",
    },
  });

  assert.deepEqual(result, {
    requestId: "request-1",
    requestSegment: "r.2",
    segmentStatus: "completed",
  });

  const completed = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    {
      request_segment: result.requestSegment,
      segment_status: result.segmentStatus,
    },
    2,
  );
  assert.equal(receipt.commandReceiptPresentation(completed).text, "✓");
});

test("explicitly failed Trigger refusal remains a receipt error", () => {
  const result = receipt.commandTriggerRejectionResult({
    type: "cmd.trigger.rejected",
    text: "The Trigger command could not be dispatched.",
    data: {
      request_id: "request-1",
      request_segment: "r.2",
      receipt_status: "failed",
      message: "The Trigger command could not be dispatched.",
    },
  });

  assert.deepEqual(result, {
    requestId: "request-1",
    requestSegment: "r.2",
    segmentStatus: "rejected",
    message: "The Trigger command could not be dispatched.",
  });

  const failed = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    {
      request_segment: result.requestSegment,
      segment_status: result.segmentStatus,
      message: result.message,
    },
    2,
  );
  const presentation = receipt.commandReceiptPresentation(failed);
  assert.equal(presentation.text, "×");
  assert.match(presentation.failureDetail, /could not be dispatched/);
});

test("uncorrelated output and control or resolution frames are not terminal", () => {
  const messages = [
    {
      type: "cmd.look.success",
      data: { request_id: "missing-segment" },
    },
    {
      type: "cmd.request.queued",
      data: { request_id: "request", request_segment: "r" },
    },
    {
      type: "cmd.trigger.completed",
      data: { request_id: "request", request_segment: "r" },
    },
    {
      type: "cmd.craft.started",
      data: { request_id: "request", request_segment: "r" },
    },
    {
      type: "cmd.alias.resolve",
      data: { request_id: "request", request_segment: "r" },
    },
    {
      type: "cmd.history.replay",
      data: { request_id: "request", request_segment: "r" },
    },
  ];

  for (const message of messages) {
    assert.equal(receipt.commandTerminalResult(message), null);
  }
});

test("command-chain plan keeps the root pending until every segment completes", () => {
  const plan = receipt.commandRequestSegments({
    type: "cmd.request.segments",
    data: {
      request_id: "request-1",
      request_segments: ["r.0", "r.1"],
    },
  });
  assert.deepEqual(plan, {
    requestId: "request-1",
    requestSegments: ["r.0", "r.1"],
  });

  let state = receipt.initialCommandReceipt(1);
  for (const requestSegment of plan.requestSegments) {
    state = receipt.transitionCommandReceipt(
      state,
      { request_segment: requestSegment, segment_status: "accepted" },
      2,
    );
  }
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.0", segment_status: "completed" },
    3,
  );
  assert.equal(receipt.commandReceiptPresentation(state).text, "…");

  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.1", segment_status: "completed" },
    4,
  );
  assert.equal(receipt.commandReceiptPresentation(state).text, "✓");
});

test("command-chain plan normalizes segments and rejects malformed controls", () => {
  assert.deepEqual(
    receipt.commandRequestSegments({
      type: "cmd.request.segments",
      data: {
        request_id: "request",
        request_segments: [" r.0 ", "", "r.0", null, "r.1"],
      },
    }),
    {
      requestId: "request",
      requestSegments: ["r.0", "r.1"],
    },
  );
  assert.equal(
    receipt.commandRequestSegments({
      type: "cmd.request.segments",
      data: { request_id: "request", request_segments: [] },
    }),
    null,
  );
});

test("late queued acknowledgement cannot regress terminal success", () => {
  const completed = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    { request_segment: "r", segment_status: "completed" },
    2,
  );
  const lateQueued = receipt.transitionCommandReceipt(
    completed,
    { phase: "received" },
    3,
  );

  assert.deepEqual(lateQueued, completed);
  assert.equal(receipt.commandReceiptPresentation(lateQueued).text, "✓");
});

test("completion can resolve uncertainty even when earlier controls were lost", () => {
  const unconfirmed = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    { phase: "unconfirmed" },
    2,
  );
  const completed = receipt.transitionCommandReceipt(
    unconfirmed,
    { request_segment: "r", segment_status: "completed" },
    3,
  );

  assert.equal(completed.phase, "accepted");
  assert.equal(completed.compact, true);
  assert.equal(receipt.commandReceiptPresentation(completed).text, "✓");
});

test("cancellation remains terminal after completion or duplicate ACKs", () => {
  const cancelled = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    { phase: "cancelled", message: "That action can no longer be completed." },
    2,
  );
  const completed = receipt.transitionCommandReceipt(
    cancelled,
    { phase: "accepted", compact: true },
    3,
  );
  const queued = receipt.transitionCommandReceipt(
    completed,
    { phase: "received" },
    4,
  );

  assert.deepEqual(completed, cancelled);
  assert.deepEqual(queued, cancelled);
  assert.equal(
    receipt.commandReceiptPresentation(queued).text,
    "×",
  );
});

test("phase-specific compaction cannot compact a newer lifecycle", () => {
  const accepted = receipt.transitionCommandReceipt(
    receipt.initialCommandReceipt(1),
    { phase: "accepted" },
    2,
  );
  const staleTimer = receipt.transitionCommandReceipt(
    accepted,
    { compact: true, expected_phase: "received" },
    3,
  );

  assert.deepEqual(staleTimer, accepted);
});

test("one completed chain segment stays underway while another is active", () => {
  let state = receipt.initialCommandReceipt(1);
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.0", segment_status: "accepted" },
    2,
  );
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.1", segment_status: "accepted" },
    3,
  );
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.0", segment_status: "completed" },
    4,
  );

  assert.equal(state.phase, "accepted");
  assert.equal(state.compact, false);
  assert.equal(receipt.commandReceiptPresentation(state).text, "…");

  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.1", segment_status: "completed" },
    5,
  );
  assert.equal(state.compact, true);
  assert.equal(receipt.commandReceiptPresentation(state).text, "✓");
});

test("late chain acceptance reopens a prematurely compact receipt", () => {
  let state = receipt.initialCommandReceipt(1);
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.0", segment_status: "completed" },
    2,
  );
  assert.equal(state.compact, true);

  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.1", segment_status: "accepted" },
    3,
  );
  assert.equal(state.compact, false);
  assert.equal(receipt.commandReceiptPresentation(state).text, "…");

  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.1", segment_status: "completed" },
    4,
  );
  assert.equal(state.compact, true);
});

test("presentation uses only compact pending, success, and error symbols", () => {
  const sending = receipt.commandReceiptPresentation(
    receipt.initialCommandReceipt(1),
  );
  const completed = receipt.commandReceiptPresentation(
    receipt.transitionCommandReceipt(
      receipt.initialCommandReceipt(1),
      { request_segment: "r", segment_status: "completed" },
      2,
    ),
  );
  const unconfirmed = receipt.commandReceiptPresentation(
    receipt.transitionCommandReceipt(
      receipt.initialCommandReceipt(1),
      {
        phase: "unconfirmed",
        message: "Unable to confirm command delivery.",
      },
      2,
    ),
  );
  const cancelled = receipt.commandReceiptPresentation(
    receipt.transitionCommandReceipt(
      receipt.initialCommandReceipt(1),
      {
        phase: "cancelled",
        message: "That action can no longer be completed.",
      },
      2,
    ),
  );

  assert.deepEqual(
    [sending.text, completed.text, unconfirmed.text, cancelled.text],
    ["…", "✓", "×", "×"],
  );
  assert.deepEqual(
    [sending.state, completed.state, unconfirmed.state, cancelled.state],
    ["pending", "success", "error", "error"],
  );
  assert.equal(sending.failureDetail, undefined);
  assert.equal(completed.failureDetail, undefined);
  assert.equal(completed.ariaLabel, "Command processed.");
  assert.equal(unconfirmed.failureDetail, unconfirmed.ariaLabel);
  assert.equal(cancelled.failureDetail, cancelled.ariaLabel);
  assert.match(cancelled.failureDetail, /Command processing failed/);
  assert.match(unconfirmed.ariaLabel, /not retried automatically/i);
  assert.match(
    cancelled.ariaLabel,
    /That action can no longer be completed/,
  );
});

test("a failed chain segment dominates completed and active segments", () => {
  let state = receipt.initialCommandReceipt(1);
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.0", segment_status: "completed" },
    2,
  );
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.1", segment_status: "accepted" },
    3,
  );
  state = receipt.transitionCommandReceipt(
    state,
    {
      request_segment: "r.0",
      segment_status: "cancelled",
      message: "That action can no longer be completed.",
    },
    4,
  );
  state = receipt.transitionCommandReceipt(
    state,
    { request_segment: "r.1", segment_status: "completed" },
    5,
  );

  assert.equal(state.phase, "cancelled");
  assert.equal(state.compact, false);
});

test("alias resolution prefers exact request identity", () => {
  const messages = [
    {
      echo: true,
      request_id: "first",
      command_resolution: {
        kind: "alias",
        original_text: "cross",
        resolved: false,
      },
    },
    {
      echo: true,
      request_id: "second",
    },
  ];
  const resolution = receipt.commandResolution({
    type: "cmd.alias.resolve",
    text: "cross -> pay charon",
    data: {
      request_id: "second",
      command: "cross",
      resolved: "pay charon",
    },
  });

  assert.equal(receipt.commandResolutionEchoIndex(messages, resolution), 1);
});

test("legacy uncorrelated resolution consumes pending echoes in order", () => {
  const messages = [
    {
      echo: true,
      request_id: "first",
      command_resolution: {
        kind: "history",
        original_text: "!1",
        resolved: false,
      },
    },
    {
      echo: true,
      request_id: "second",
      command_resolution: {
        kind: "history",
        original_text: "!1",
        resolved: false,
      },
    },
  ];
  const resolution = receipt.commandResolution({
    type: "cmd.history.replay",
    text: "!1 -> pay charon",
    data: {
      reference: "!1",
      command: "pay charon",
    },
  });

  assert.equal(receipt.commandResolutionEchoIndex(messages, resolution), 0);
  messages[0].command_resolution.resolved = true;
  assert.equal(receipt.commandResolutionEchoIndex(messages, resolution), 1);
});

test("uncorrelated resolution does not reuse an old resolved echo", () => {
  const messages = [{
    echo: true,
    request_id: "request",
    command_resolution: {
      kind: "alias",
      original_text: "cross",
      resolved: true,
    },
  }];
  const resolution = receipt.commandResolution({
    type: "cmd.alias.resolve",
    text: "cross -> pay charon",
    data: {
      command: "cross",
      resolved: "pay charon",
    },
  });

  assert.equal(receipt.commandResolutionEchoIndex(messages, resolution), -1);
});
