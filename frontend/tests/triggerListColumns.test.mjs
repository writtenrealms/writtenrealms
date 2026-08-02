import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [worldTriggerListSource, roomTriggerListSource] = await Promise.all([
  readSource("../src/views/builder/world/TriggerList.vue"),
  readSource("../src/views/builder/room/RoomTriggerList.vue"),
]);

const listSchema = (source) => {
  const match = source.match(/const listSchema:[\s\S]*?= \[(?<schema>[\s\S]*?)\n\];/);
  assert.ok(match?.groups?.schema);
  return match.groups.schema;
};

test("Trigger list tables omit operational order, delay, and active columns", () => {
  const worldSchema = listSchema(worldTriggerListSource);
  const roomSchema = listSchema(roomTriggerListSource);

  for (const schema of [worldSchema, roomSchema]) {
    assert.doesNotMatch(schema, /name:\s*"is_active"/);
  }
  assert.doesNotMatch(roomSchema, /name:\s*"order"/);
  assert.doesNotMatch(roomSchema, /name:\s*"gate_delay"/);
});
