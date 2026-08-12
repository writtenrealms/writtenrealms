import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [
  referenceFieldSource,
  whoSource,
  exitsSource,
  roomSource,
  zoneSource,
  pathSource,
  gameStoreSource,
  routerSource,
] = await Promise.all([
  readSource("../src/components/forms/ReferenceField.vue"),
  readSource("../src/components/game/console/Who.vue"),
  readSource("../src/components/game/console/Exits.vue"),
  readSource("../src/views/builder/room/Room.vue"),
  readSource("../src/views/builder/zone/Zone.vue"),
  readSource("../src/views/builder/zone/PathDetails.vue"),
  readSource("../src/store/modules/game.ts"),
  readSource("../src/router/index.ts"),
]);

test("room reference pickers label rooms with portable identity", () => {
  assert.match(referenceFieldSource, /model_type\.value !== "room"/);
  assert.match(referenceFieldSource, /data\.manifest_ref/);
  assert.match(referenceFieldSource, /`room@\$\{data\.relative_id\}`/);
  assert.match(referenceFieldSource, /return "Room ref unavailable"/);
  assert.match(referenceFieldSource, /referenceLabel\(result\)/);
  assert.match(referenceFieldSource, /data\.instance_scope/);
  assert.match(referenceFieldSource, /\$\{data\.instance_scope\}\/\$\{identity\}/);
  assert.doesNotMatch(referenceFieldSource, /\{\{\s*result\.id\s*\}\}/);
  assert.doesNotMatch(referenceFieldSource, /model_type\.value === "room"[^]*return data\.id/);
});

test("runtime builder location displays use room manifest refs", () => {
  assert.match(whoSource, /player\.room_manifest_ref/);
  assert.doesNotMatch(whoSource, /player\.room_id/);
  assert.match(exitsSource, /room_data\.manifest_ref/);
  assert.doesNotMatch(exitsSource, /room_data\.id/);
});

test("builder screens reserve database IDs for staff diagnostics", () => {
  for (const source of [roomSource, zoneSource, pathSource]) {
    assert.match(source, /v-if="store\.state\.auth\.user\.is_staff"[^>]*>\s*(?:\n\s*)?(?:<dt>|Database ID)/);
  }
  assert.doesNotMatch(roomSource, /Deleted \$\{room_ref\} \(database ID/);
});

test("game movement has no hard-coded legacy room database identity", () => {
  assert.doesNotMatch(gameStoreSource, /10129/);
  assert.doesNotMatch(gameStoreSource, /Hardcode for Cave/);
});

test("explicit database lookup routes are staff diagnostics", () => {
  assert.match(routerSource, /zones\/db\/:zone_database_id[^\n]+beforeEnter: ifStaff/);
  assert.match(routerSource, /rooms\/db\/:room_database_id[^\n]+beforeEnter: ifStaff/);
});
