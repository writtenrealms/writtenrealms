import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [configSource, utilsSource, zoneSource, routerSource, frameSource, manifestServiceSource] = await Promise.all([
  readSource("../src/views/builder/zone/Config.vue"),
  readSource("../src/views/builder/zone/Utils.vue"),
  readSource("../src/views/builder/zone/Zone.vue"),
  readSource("../src/router/index.ts"),
  readSource("../src/components/builder/BuilderFrame.vue"),
  readSource("../src/services/manifests.ts"),
]);

test("Zone Config edits and reloads the canonical zone manifest", () => {
  assert.match(configSource, /import ManifestYamlEditor from/);
  assert.match(configSource, /v-model="manifestText"/);
  assert.match(configSource, /:loaded-value="loadedYaml"/);
  assert.match(configSource, /:is-submitting="isSubmitting"/);
  assert.match(configSource, /:min-height="500"/);
  assert.match(configSource, /@save="saveZoneYaml"/);
  assert.match(
    configSource,
    /applyWorldManifest\([\s\S]*?submittedYaml,[\s\S]*?"zone",[\s\S]*?`zone@\$\{saveRoute\.zoneRelativeId\}`,[\s\S]*?"apply",[\s\S]*?"updated",[\s\S]*?\)/,
  );
  assert.match(configSource, /builder\/zone_relative_fetch/);
  assert.match(configSource, /commit_zone: false/);
  assert.match(configSource, /cancelToken: cancellation\.token/);
  assert.match(configSource, /axios\.CancelToken\.source\(\)/);
  assert.match(configSource, /let zoneYamlLoadId = 0/);
  assert.match(configSource, /loadId === zoneYamlLoadId && routeStillMatches\(identity\)/);
  assert.match(configSource, /let zoneYamlSaveId = 0/);
  assert.match(configSource, /saveId === zoneYamlSaveId && routeStillMatches\(identity\)/);
  assert.match(
    configSource,
    /String\(payload\?\.relative_id\) !== identity\.zoneRelativeId/,
  );
  assert.match(configSource, /store\.commit\("builder\/zone_set", payload\)/);
  assert.match(
    configSource,
    /\(\) => \[route\.params\.world_id, route\.params\.zone_relative_id\]/,
  );
  assert.doesNotMatch(configSource, /currentZone\?\.yaml|currentZoneMatchesRoute/);
  assert.match(configSource, /Zone YAML was saved, but its updated state could not be reloaded/);
  assert.match(manifestServiceSource, /\| "zone"/);
  assert.match(manifestServiceSource, /expected_ref\?: string/);
  assert.match(manifestServiceSource, /expected_operation\?: ManifestOperation/);
  assert.match(manifestServiceSource, /expected_result\?: "updated"/);
  assert.match(manifestServiceSource, /payload\.expected_ref = expectedRef/);
  assert.match(manifestServiceSource, /payload\.expected_operation = expectedOperation/);
  assert.match(manifestServiceSource, /payload\.expected_result = expectedResult/);
  assert.match(
    configSource,
    /builder_info\?\.builder_rank > 2[\s\S]*?routeZoneMatchesStore\.value[\s\S]*?zone\.value\.has_assignment === true/,
  );
  assert.match(configSource, /if \(!canConfigureZone\.value\)/);
  assert.match(
    configSource,
    /watch\(\s*canConfigureZone,[\s\S]*?previouslyCouldConfigure === true[\s\S]*?loadZoneYaml\(currentRouteIdentity\(\)\)[\s\S]*?\{ immediate: true \}/,
  );
  assert.doesNotMatch(configSource, /onMounted\(\(\) => loadZoneYaml\(\)\)/);
});

test("Move Zone lives under the dedicated Zone Utils route", () => {
  assert.doesNotMatch(configSource, /MOVE ZONE|builder\/zones\/move_zone/);
  assert.match(utilsSource, /<h2>ZONE UTILS<\/h2>/);
  assert.match(utilsSource, /<h3>MOVE ZONE<\/h3>/);
  assert.match(utilsSource, /action: "builder\/zones\/move_zone"/);
  assert.match(utilsSource, /builder_info\.builder_rank > 2/);
  assert.match(
    routerSource,
    /path: 'zones\/:zone_relative_id\(\\\\d\+\)\/utils', name: 'builder_zone_utils'/,
  );
  assert.match(frameSource, /name: 'builder_zone_utils'/);
  assert.match(frameSource, />Utils<\/router-link>/);
  assert.match(
    frameSource,
    /v-if="world\?\.builder_info\?\.builder_rank > 2"[\s\S]*?name: 'builder_zone_utils'/,
  );
  assert.match(frameSource, /const isZoneUtilsRoute = computed/);
});

test("Zone overview treats typed policies as read-only configuration", () => {
  assert.doesNotMatch(zoneSource, /respawn_wait/);
  assert.doesNotMatch(zoneSource, /onChangeRespawns|editRespawns/);
  assert.doesNotMatch(zoneSource, /store\.dispatch\("builder\/zone_save",\s*\{\s*respawn_wait/);
  assert.match(zoneSource, /formatPolicy\(zone\.respawn\)/);
  assert.match(zoneSource, /formatPolicy\(zone\.door_reset\)/);
  assert.match(zoneSource, /spec\.respawn =/);
  assert.match(zoneSource, /spec\.door_reset = zoneValue\.door_reset/);
  assert.match(zoneSource, /name: 'builder_zone_config'/);
});
