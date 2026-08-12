import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [detailSource, sharedDetailsSource, merchantServiceSource] = await Promise.all([
  readSource("../src/views/builder/world/MerchantProfileDetails.vue"),
  readSource("../src/components/builder/world/ManifestResourceDetails.vue"),
  readSource("../src/services/merchants.ts"),
]);

test("merchant profile details use the shared inline manifest editor", () => {
  assert.match(detailSource, /<ManifestResourceDetails/);
  assert.match(detailSource, /expected-kind="merchantprofile"/);
  assert.match(detailSource, /response-field="merchant_profile"/);
  assert.match(detailSource, /list-route-name="builder_merchant_profile_list"/);
  assert.match(detailSource, /detail-route-name="builder_merchant_profile_details"/);
  assert.match(detailSource, /detail-id-param="merchant_profile_id"/);
  assert.match(detailSource, /:inherited-world="inheritedWorld"/);
  assert.doesNotMatch(detailSource, /builder_world_edit|prefill:\s*"merchant-profile"/);
  assert.doesNotMatch(detailSource, /<textarea[^>]*\breadonly\b/);
});

test("merchant profile details load through a typed merchant service", () => {
  assert.match(detailSource, /import \{[^}]*fetchMerchantProfile[^}]*\} from "@\/services\/merchants"/);
  assert.match(merchantServiceSource, /Promise<MerchantProfileDetail>/);
  assert.match(merchantServiceSource, /merchantProfileDetailEndpoint\(worldId, profileId\)/);
});

test("merchant summaries stay current with manifest apply responses", () => {
  assert.match(detailSource, /Array\.isArray\(profile\.stock\)/);
  assert.match(detailSource, /return slots\.length/);
  assert.match(detailSource, /stockSummary\(profile\)/);
});

test("shared manifest details use resource-generic copy and mutation behavior", () => {
  assert.match(
    sharedDetailsSource,
    /\{\{ listLabel \}\} in this instance are inherited from/,
  );
  assert.match(sharedDetailsSource, /params: \{ world_id: worldId \}/);
  assert.match(
    sharedDetailsSource,
    /applyWorldManifest\(\s*props\.worldId,\s*manifestText\.value,\s*props\.expectedKind,/,
  );
  assert.match(sharedDetailsSource, /response\.kind !== props\.expectedKind/);
  assert.match(sharedDetailsSource, /response\[props\.responseField\]/);
  assert.match(sharedDetailsSource, /setLoadedState\(appliedResource\)/);
  assert.match(sharedDetailsSource, /response\.operation === "deleted"/);
  assert.match(sharedDetailsSource, /name: props\.listRouteName/);
  assert.match(sharedDetailsSource, /resource\.delete_yaml/);
  assert.match(sharedDetailsSource, /@click="copyDeleteYaml"/);
  assert.doesNotMatch(sharedDetailsSource, /Crafting definitions|Unexpected crafting manifest/);
});

test("shared manifest details are read-only for inherited and lower-rank builders", () => {
  assert.match(sharedDetailsSource, /isInherited\.value \|\| builderRank\.value <= 2/);
  assert.match(sharedDetailsSource, /v-else-if="isReadOnly"/);
  assert.match(sharedDetailsSource, /your builder role cannot edit it/);
});
