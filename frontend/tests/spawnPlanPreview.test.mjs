import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { formatSpawnCount } = await server.ssrLoadModule('/src/core/spawnPlanPreview.ts');
const { default: SpawnPlanPreview } = await server.ssrLoadModule('/src/components/builder/zone/SpawnPlanPreview.vue');
const { default: MobDefinitionPreview } = await server.ssrLoadModule('/src/components/builder/world/MobDefinitionPreview.vue');

test('spawn quantities match fixed, ranged, and default runtime counts', () => {
  for (const [input, expected] of [
    [0, '0'], [3, '3'], ['2', '2'], [{}, '1'], [{ value: 4 }, '4'],
    [{ min: 2, max: 7 }, '2–7'], [{ min: 3, max: 3 }, '3'], [{ max: 4 }, '0–4'],
  ]) assert.equal(formatSpawnCount(input), expected);
});

test('plans start collapsed with an accessible native disclosure and inactive status', async () => {
  const html = await renderToString(createSSRApp(SpawnPlanPreview, {
    worldId: 15, zoneId: 2, zoneRelativeId: 2,
    plan: { id: 1, slug: 'field', name: 'Battlefield Spawns', num_entries: 7, is_active: false },
  }));
  assert.match(html, /<details[^>]*><summary[^>]*>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(html, /Battlefield Spawns/);
  assert.match(html, /7 entries/);
  assert.match(html, /Inactive/);
  assert.doesNotMatch(html, /Loading spawns/);
});

test('mob names are escaped and keyboard accessible without eagerly rendering details', async () => {
  const html = await renderToString(createSSRApp(MobDefinitionPreview, {
    worldId: 15,
    mob: { id: 1, slug: 'guard', name: '<script>guard</script>', type: 'humanoid',
      description: 'Details render when opened.', attributes: {}, base_properties: {} },
  }));
  assert.match(html, /<button[^>]*aria-haspopup="dialog"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /&lt;script&gt;guard&lt;\/script&gt;/);
  assert.doesNotMatch(html, /Details render when opened/);
});
