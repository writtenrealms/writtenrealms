import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const editWorldSource = await readFile(
  new URL("../src/views/builder/world/EditWorld.vue", import.meta.url),
  "utf8",
);

const editorMatch = editWorldSource.match(
  /<ManifestYamlEditor(?<editor>[\s\S]*?)<\/ManifestYamlEditor>/,
);

test("World Edit uses the shared manifest detail editor", () => {
  assert.ok(editorMatch?.groups?.editor);
  assert.match(editWorldSource, /import ManifestYamlEditor from/);
  assert.match(editorMatch.groups.editor, /v-model="manifestText"/);
  assert.match(editorMatch.groups.editor, /:is-submitting="isSubmitting"/);
  assert.match(editorMatch.groups.editor, /save-label="APPLY MANIFEST"/);
  assert.match(editorMatch.groups.editor, /saving-label="APPLYING\.\.\."/);
  assert.match(editorMatch.groups.editor, /@save="submitManifest"/);
});

test("World Edit keeps concise batch guidance without an inline kind catalog", () => {
  assert.match(
    editorMatch.groups.editor,
    /Paste one or more YAML manifests\. Each YAML document is applied in order\./,
  );
  assert.doesNotMatch(editWorldSource, /Supported kinds:/i);
  assert.doesNotMatch(editWorldSource, /class="manifest-input"/);
  assert.match(
    editorMatch.groups.editor,
    /href="https:\/\/docs\.writtenrealms\.com\/builders\/yaml-manifests"/,
  );
  assert.match(editorMatch.groups.editor, /View supported kinds and examples\./);
});

test("World Edit preserves the post-apply result workflow", () => {
  assert.match(editWorldSource, /Manifest Applied/);
  assert.match(editWorldSource, /APPLY ANOTHER MANIFEST/);
  assert.match(editWorldSource, /aria-live="polite"/);
});
