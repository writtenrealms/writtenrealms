import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createSSRApp } from "vue";
import { createStore } from "vuex";
import { renderToString } from "vue/server-renderer";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { default: Console } = await server.ssrLoadModule("/src/components/game/console/Console.vue");
const text = "You are in a time-controlled instance, where combat can wait for you between rounds. "
  + "Use `pause` anytime, `advance` for the next round, or `resume` for normal timing.";
const hint = { type: "notification.instance.time_control_hint", message_id: "hint", text, data: {} };
const completion = { type: "instance.completed", message_id: "completion",
  text: "A Persian Outpost completed in 853.461 seconds.",
  data: { template_name: "A Persian Outpost", clear_time_ms: 853461 } };
const room = { id: 1, name: "A Quiet Clearing", description: "The path opens into a clearing.",
  inventory: [], chars: [], details: [], actions: [], quest_callouts: [] };
const snapshot = { type: "cmd.state.sync.success", message_id: "room", data: { room, world: {} } };

const render = (messages, is_mobile = false) => renderToString(createSSRApp(Console).use(createStore({
  state: { game: { is_mobile, player: {}, player_config: {}, room,
    motd: "Welcome to the realm.", last_viewed_room_message: snapshot } },
  getters: { "game/consoleMessages": () => messages },
})));

test("completion and time-control entry share the MOTD notice on desktop and mobile", async () => {
  for (const mobile of [false, true]) {
    const html = await render([snapshot, hint, completion], mobile);
    assert.equal(html.match(/class="console-notice /g)?.length, 3);
    assert.deepEqual(Array.from(html.matchAll(/class="console-notice-title mb-4"[^>]*>(.*?)<\/div>/g),
      match => match[1]), ["Message of the Day", "Time Control", "Instance Complete"]);
    assert.match(html, /A Persian Outpost completed in 14m 13\.5s\./);
    assert.doesNotMatch(html, /853\.461 seconds/);
    assert.match(html, /Welcome to the realm\./);
    assert.match(html, /You are in a time-controlled instance/);
    assert.deepEqual(Array.from(html.matchAll(/<code[^>]*>(.*?)<\/code>/g),
      match => match[1]), ["pause", "advance", "resume"]);
    assert.doesNotMatch(html, /`pause`|`advance`|`resume`/);
  }
});

test("notice text is escaped and ordinary command confirmations stay plain", async () => {
  const html = await render([
    { ...hint, text: "<img src=x onerror=alert(1)> Use `pause` & think." },
    { ...completion, data: { ...completion.data, template_name: "<script>alert(1)</script>" } },
    { type: "cmd.time_control.success", message_id: "confirmation", text: "Combat pauses before each round.", data: {} },
  ]);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.equal(html.match(/class="console-notice /g)?.length, 2);
  assert.match(html, /Combat pauses before each round\./);
});

test("completion notices use ranking precision across minute and hour boundaries", async () => {
  for (const [time, expected] of [
    [0, '0.0s'], [59949, '59.9s'], [59950, '1m 0.0s'], [3599950, '1h 0m 0.0s'],
  ]) {
    const html = await render([{ ...completion, data: { ...completion.data, clear_time_ms: time } }]);
    assert.ok(html.includes(`A Persian Outpost completed in ${expected}.`));
  }
});

test("completion notices retain server text when structured timing is unavailable", async () => {
  for (const data of [undefined, {}, { template_name: 'A Persian Outpost' },
    { ...completion.data, clear_time_ms: null }, { ...completion.data, clear_time_ms: -1 },
    { ...completion.data, clear_time_ms: NaN }]) {
    const html = await render([{ ...completion, data, text: '<Outpost> completed.' }]);
    assert.match(html, /&lt;Outpost&gt; completed\./);
  }
});

test("completed status appears below the instance ID on entry and reconnect snapshots", async () => {
  for (const mobile of [false, true]) {
    for (const type of ["cmd.state.sync.success", "system.connect.success"]) {
      const html = await render([{ ...snapshot, type, data: { room, world: {
        instance_ref: "completed-run-id", instance_status: "completed",
      } } }], mobile);
      assert.match(html, /Instance ID: completed-run-id<\/div>\s*<div[^>]*> Status: Completed <\/div>/);
    }
  }
});

test("fresh runs and ordinary worlds never display a completed label", async () => {
  for (const world of [{}, { instance_ref: "fresh-run-id", instance_status: "active" },
    { instance_ref: "old-payload-without-status" }]) {
    assert.doesNotMatch(await render([{ ...snapshot, data: { room, world } }]), /Status: Completed/);
  }
});
