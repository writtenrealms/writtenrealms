import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [source, elementListSource] = await Promise.all([
  readFile(
    new URL("../src/views/builder/world/CraftingRecipeList.vue", import.meta.url),
    "utf8",
  ),
  readFile(new URL("../src/components/elementlist/ElementList.vue", import.meta.url), "utf8"),
]);

const schema = source.match(/const listSchema:[\s\S]*?= \[(?<schema>[\s\S]*?)\n\];/)
  ?.groups?.schema;

assert.ok(schema);

test("crafting recipe list hides Output on mobile", () => {
  assert.match(schema, /name: "name"[\s\S]*?mobileHidden: true/);
});

test("crafting recipe list omits Inputs, Fee, and Order", () => {
  for (const field of ["ingredient_count", "money", "order"]) {
    assert.doesNotMatch(schema, new RegExp(`name: "${field}"`));
  }
});

test("crafting recipe list uses Group as a filter instead of a column", () => {
  assert.doesNotMatch(schema, /name: "group"/);
  assert.match(source, /label: "Group",\s*attr: "group"/);
  assert.match(source, /filter_options: \[\]/);
  assert.match(source, /import ElementList from/);
  assert.match(elementListSource, /paginated_data\.value\?\.filter_options/);
});

test("crafting recipe filter is capped at half width on desktop", () => {
  assert.match(source, /class="half-width-filters"/);

  const modifierStyles = elementListSource.match(
    /@media \(\$desktop-site\) \{\s*&\.half-width-filters \{\s*\.resource-filter-select \{(?<styles>[\s\S]*?)\n\s*\}\s*\}/,
  )?.groups?.styles;

  assert.ok(modifierStyles);
  assert.match(modifierStyles, /flex:\s*0 1 50%/);
  assert.match(modifierStyles, /max-width:\s*50%/);
  assert.match(modifierStyles, /min-width:\s*0/);
});
