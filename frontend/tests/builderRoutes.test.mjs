import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(
  new URL("../src/core/builderRoutes.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const builderRoutes = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("zone routes prefer the world-relative identity", () => {
  assert.deepEqual(
    builderRoutes.builderZoneIndexRoute(23, {
      id: 38,
      relative_id: 5,
      manifest_ref: "zone@5",
    }),
    {
      name: "builder_zone_index",
      params: {
        world_id: 23,
        zone_relative_id: "5",
      },
    },
  );
});

test("portable zone refs can supply the relative route identity", () => {
  assert.equal(builderRoutes.zoneRelativeId({ id: 38, ref: "zone@5" }), "5");
  assert.equal(builderRoutes.zoneRelativeIdFromRef("zone@5"), "5");
  assert.equal(builderRoutes.zoneRelativeIdFromRef("zone.38"), null);
});

test("zone database identity uses only the explicit lookup route", () => {
  assert.deepEqual(
    builderRoutes.builderZoneIndexRoute(23, { id: 38 }),
    {
      name: "builder_zone_database_lookup",
      params: {
        world_id: 23,
        zone_database_id: 38,
      },
    },
  );
});

test("the world zone list is not treated as a selected-zone context", () => {
  assert.equal(
    builderRoutes.isBuilderZoneContextRoute("builder_zone_list", undefined),
    false,
  );
  assert.equal(
    builderRoutes.isBuilderZoneContextRoute("builder_zone_database_lookup", undefined),
    false,
  );
  assert.equal(
    builderRoutes.isBuilderZoneContextRoute("builder_zone_room_list", "5"),
    true,
  );
});

test("room routes prefer the world-relative identity", () => {
  assert.deepEqual(
    builderRoutes.builderRoomIndexRoute(23, {
      id: 183,
      relative_id: 3,
      manifest_ref: "room@3",
    }),
    {
      name: "builder_room_index",
      params: {
        world_id: 23,
        room_relative_id: "3",
      },
    },
  );
});

test("portable room refs can supply the relative route identity", () => {
  assert.equal(builderRoutes.roomRelativeId({ id: 183, ref: "room@3" }), "3");
  assert.equal(builderRoutes.roomRelativeIdFromRef("room@3"), "3");
  assert.equal(builderRoutes.roomRelativeIdFromRef("room.183"), null);
  assert.equal(builderRoutes.roomRelativeIdFromRef("room@0,0,100"), null);
});

test("map room lookup never sends an undefined relative ID", () => {
  assert.equal(
    builderRoutes.builderRoomDetailEndpoint(23, { id: 183, relative_id: 3 }),
    "/builder/worlds/23/rooms/by-relative-id/3/",
  );
  assert.equal(
    builderRoutes.builderRoomDetailEndpoint(23, { id: 183 }),
    "/builder/worlds/23/rooms/183/",
  );
  assert.equal(builderRoutes.builderRoomDetailEndpoint(23, {}), null);
});

test("the database room redirect is not treated as a selected-room context", () => {
  assert.equal(
    builderRoutes.isBuilderRoomContextRoute("builder_room_database_lookup", undefined),
    false,
  );
  assert.equal(
    builderRoutes.isBuilderRoomContextRoute("builder_room_index", "3"),
    true,
  );
});

test("database identity uses only the explicit lookup route", () => {
  assert.deepEqual(
    builderRoutes.builderRoomIndexRoute(23, { id: 183 }),
    {
      name: "builder_room_database_lookup",
      params: {
        world_id: 23,
        room_database_id: 183,
      },
    },
  );
});
