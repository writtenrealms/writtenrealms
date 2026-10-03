import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { questRemainingSeconds, questReadyAtTitle } = await server.ssrLoadModule("/src/core/questRepeatability.ts");
const wallTime = Date.parse("2026-10-02T08:59:00Z");
const daily = {
  mode: "daily", state: "waiting", ready_at: "2026-10-02T09:00:00Z",
  remaining_seconds: 60, timezone: "America/New_York", reset_at: "05:00",
};
const clock = {
  control: { enabled: true, paused: true, simulation_time: "2026-10-02T08:00:00Z", clock_offset_seconds: 1800 },
  nowMs: wallTime + 30000, fetchedAtMs: wallTime,
  gameplayAtFetchMs: Date.parse("2026-10-02T07:30:00Z"),
  serverTimeAtFetchMs: Date.parse("2026-10-02T07:30:00Z"),
  wallTimeAtFetchMs: wallTime, offsetAtFetchMs: 1800000,
};

test("daily countdown continues while gameplay is paused and becomes ready exactly at reset", () => {
  assert.equal(questRemainingSeconds(daily, clock), 30);
  assert.equal(questRemainingSeconds(daily, { ...clock, nowMs: wallTime + 60000 }), 0);
  assert.equal(questRemainingSeconds(daily, { ...clock, nowMs: wallTime + 90000 }), 0);
});

test("daily timestamps ignore the device clock offset and gameplay clock rebases", () => {
  assert.equal(questRemainingSeconds(daily, {
    ...clock, fetchedAtMs: wallTime + 3600000, nowMs: wallTime + 3630000,
    control: { ...clock.control, paused: false, clock_offset_seconds: 7200 },
  }), 30);
});

test("ordinary cooldowns still stop with gameplay and follow manually advanced time", () => {
  const cooldown = { ...daily, mode: "cooldown", ready_at: "2026-10-02T07:31:00Z" };
  assert.equal(questRemainingSeconds(cooldown, clock), 60);
  assert.equal(questRemainingSeconds(cooldown, {
    ...clock, control: { ...clock.control, simulation_time: "2026-10-02T08:00:20Z" },
  }), 40);
});

test("missing server timestamps fall back to remaining seconds using the appropriate clock", () => {
  const fallback = { ...clock, wallTimeAtFetchMs: null, serverTimeAtFetchMs: null };
  assert.equal(questRemainingSeconds(daily, fallback), 30);
  assert.equal(questRemainingSeconds({ ...daily, mode: "cooldown" }, fallback), 60);
  assert.equal(questRemainingSeconds({ ...daily, state: "unavailable" }, fallback), 0);
});

test("daily tooltip uses the schedule timezone even inside a paused instance", () => {
  const title = questReadyAtTitle(daily, clock);
  assert.match(title, /^Daily reset at /);
  assert.match(title, /America\/New_York/);
  assert.doesNotMatch(title, /instance time/);
  assert.equal(questReadyAtTitle(daily, {
    ...clock, control: { ...clock.control, clock_offset_seconds: 7200 },
  }), title);
  assert.equal(questReadyAtTitle({ ...daily, mode: "cooldown" }, clock), "This cooldown advances with instance time.");
});

test("daily tooltip falls back to local time when the browser cannot format the timezone", () => {
  assert.equal(
    questReadyAtTitle({ ...daily, timezone: "Factory" }, clock),
    `Ready at ${new Date(daily.ready_at).toLocaleString()}`,
  );
});
