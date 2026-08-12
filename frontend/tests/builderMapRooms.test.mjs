import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [
  builderStoreSource,
  builderFrameSource,
  roomViewSource,
  zoneViewSource,
  pathViewSource,
] = await Promise.all([
  readSource("../src/store/modules/builder/index.ts"),
  readSource("../src/components/builder/BuilderFrame.vue"),
  readSource("../src/views/builder/room/Room.vue"),
  readSource("../src/views/builder/zone/Zone.vue"),
  readSource("../src/views/builder/zone/PathDetails.vue"),
]);

test("world-map room selection has no implicit database fallback", () => {
  assert.match(builderStoreSource, /builderRoomDetailEndpoint/);
  assert.doesNotMatch(builderStoreSource, /room_database_id:\s*room\.id/);
  assert.doesNotMatch(
    builderStoreSource,
    /by-relative-id\/\$\{room_relative_id\}/,
  );
});

test("room map and breadcrumb navigation use the shared room route resolver", () => {
  assert.match(roomViewSource, /router\.push\(builderRoomIndexRoute\(/);
  assert.match(builderFrameSource, /:to="roomIndexRoute"/);
});

test("zone and path map navigation use the shared zone route resolver", () => {
  assert.match(zoneViewSource, /router\.push\(builderZoneIndexRoute\(/);
  assert.match(pathViewSource, /router\.push\(builderZoneIndexRoute\(/);
});
