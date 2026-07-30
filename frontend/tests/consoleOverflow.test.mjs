import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const consoleSource = await readFile(
  new URL("../src/components/game/console/Console.vue", import.meta.url),
  "utf8",
);

test("console messages do not become nested vertical scroll containers", () => {
  const messageRule = consoleSource.match(
    /\.message\s*\{(?<rule>[\s\S]*?)\/\/ Standalone console entries/,
  );

  assert.ok(messageRule?.groups?.rule);
  assert.doesNotMatch(messageRule.groups.rule, /overflow-x:\s*hidden/);
  assert.equal(
    messageRule.groups.rule.match(/overflow-x:\s*clip/g)?.length,
    2,
  );
});

test("say messages inherit the normal console text color", () => {
  const highlightedMessageRule = consoleSource.match(
    /\/\/ Chat, yell,[\s\S]*?(?<selectors>&\.notification[\s\S]*?)\{\s*color:\s*\$color-secondary;/,
  );

  assert.ok(highlightedMessageRule?.groups?.selectors);
  assert.doesNotMatch(
    highlightedMessageRule.groups.selectors,
    /(?:notification\\\.cmd\\\.say|cmd\\\.say)\\\.success/,
  );
});
