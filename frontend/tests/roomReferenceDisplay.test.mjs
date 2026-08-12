import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const lookRoomSource = await readFile(
  new URL("../src/components/game/console/LookRoom.vue", import.meta.url),
  "utf8",
);

test("builder room headers display the stable room manifest ref", () => {
  assert.match(
    lookRoomSource,
    /v-if="player\.is_builder && room\.manifest_ref"/,
  );
  assert.match(
    lookRoomSource,
    /\[ \{\{ room\.manifest_ref \}\} \]/,
  );
  assert.doesNotMatch(
    lookRoomSource,
    /\[ \{\{ room\.id \}\} \]/,
  );
});
