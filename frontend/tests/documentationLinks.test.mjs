import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const sources = await Promise.all([
  "../src/components/builder/world/ReviewInstructions.vue",
  "../src/core/forms.ts",
  "../src/views/builder/room/RoomActionList.vue",
  "../src/views/builder/world/EditWorld.vue",
  "../src/views/builder/world/SocialList.vue",
].map(async (path) => readFile(new URL(path, import.meta.url), "utf8")));

const combinedSource = sources.join("\n");

test("builder help links use the published VitePress guide routes", () => {
  assert.doesNotMatch(combinedSource, /docs\.writtenrealms\.com\/(?:building|playing)\//);
  assert.match(combinedSource, /docs\.writtenrealms\.com\/builders\/yaml-manifests/);
  assert.match(combinedSource, /docs\.writtenrealms\.com\/builders\/condition-builder-guide/);
  assert.match(combinedSource, /docs\.writtenrealms\.com\/builders\/social-builder-guide/);
  assert.match(combinedSource, /docs\.writtenrealms\.com\/builders\/room-actions/);
  assert.match(combinedSource, /docs\.writtenrealms\.com\/builders\/world-publishing/);
});
