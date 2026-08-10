import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [configSource, manifestServiceSource, editorSource] = await Promise.all([
  readSource("../src/views/builder/world/Config.vue"),
  readSource("../src/services/manifests.ts"),
  readSource("../src/components/builder/world/ManifestYamlEditor.vue"),
]);

const editorMatch = configSource.match(
  /<ManifestYamlEditor(?<editor>[\s\S]*?)<\/ManifestYamlEditor>/,
);

test("World Config uses the shared editable YAML editor", () => {
  assert.ok(editorMatch?.groups?.editor);
  assert.match(configSource, /import ManifestYamlEditor from/);
  assert.match(editorMatch.groups.editor, /v-model="manifestText"/);
  assert.match(editorMatch.groups.editor, /:loaded-value="loadedConfigYaml"/);
  assert.match(editorMatch.groups.editor, /:is-submitting="isSubmitting"/);
  assert.match(editorMatch.groups.editor, /:min-height="500"/);
  assert.match(editorMatch.groups.editor, /@save="saveConfigYaml"/);
  assert.match(editorMatch.groups.editor, /<h2>World YAML<\/h2>/);
  assert.match(editorSource, /:disabled="disabled \|\| isSubmitting"/);
});

test("World Config saves only a world manifest and reloads canonical YAML", () => {
  assert.match(manifestServiceSource, /ManifestResourceKind\s*=\s*\n\s*\| "world"/);
  assert.match(
    configSource,
    /applyWorldManifest\(\s*world\.value\.id,\s*submittedYaml,\s*"world",\s*\)/,
  );
  assert.match(configSource, /Promise\.allSettled/);
  assert.match(configSource, /loadedConfigYaml\.value = submittedYaml/);
  assert.match(configSource, /World YAML was saved, but its updated state could not be reloaded/);
  assert.match(configSource, /setLoadedConfigYaml\(payload\)/);
  assert.match(configSource, /manifestApiErrorMessage\(error, fallback\)/);
});

test("World Config alphabetizes the visible link cards", () => {
  assert.match(
    configSource,
    /\.filter\(\(link\) => !link\.rootOnly \|\| isRootWorld\.value\)\s*\.sort\(\(left, right\) => left\.title\.localeCompare\(right\.title\)\)/,
  );
});

test("World Config no longer renders the World Data tables or toggle", () => {
  assert.doesNotMatch(configSource, />World Data</);
  assert.doesNotMatch(configSource, /ManifestValue/);
  assert.doesNotMatch(configSource, /manifestSections/);
  assert.doesNotMatch(configSource, /showConfigYaml/);
  assert.doesNotMatch(configSource, /SHOW YAML|HIDE YAML/);
  assert.doesNotMatch(configSource, /<table\b/);
});
