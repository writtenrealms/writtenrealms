import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const sources = await Promise.all([
  readFile(new URL("../src/components/elementlist/ElementList.vue", import.meta.url), "utf8"),
  readFile(
    new URL("../src/components/builder/world/CraftingResourceList.vue", import.meta.url),
    "utf8",
  ),
]);

test("list search and pagination controls stay on one desktop row", () => {
  for (const source of sources) {
    const controlStyles = source.match(
      /\.pagination-or-search\s*\{(?<styles>[\s\S]*?)\n\s*\.actions\s*\{/,
    )?.groups?.styles;

    assert.ok(controlStyles);
    assert.match(controlStyles, /justify-content:\s*flex-end/);
    assert.match(controlStyles, /flex:\s*0 1 22rem/);
    assert.match(controlStyles, /width:\s*22rem/);
    assert.doesNotMatch(
      controlStyles.split("@media ($mobile-site)")[0],
      /flex-wrap:\s*wrap/,
    );
  }
});

test("list search controls remain responsive on mobile", () => {
  for (const source of sources) {
    assert.match(source, /flex-wrap:\s*wrap/);
    assert.match(source, /flex-basis:\s*100%/);
    assert.match(source, /width:\s*100%/);
  }
});
