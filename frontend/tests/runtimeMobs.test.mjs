import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createRouter, createMemoryHistory } from 'vue-router';
import { mob } from './fixtures/runtimeMobs.mjs';

const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), server: { middlewareMode: true, watch: null, hmr: false }, optimizeDeps: { noDiscovery: true, include: [] } });
after(() => server.close());
const { default: Details } = await server.ssrLoadModule('/src/components/builder/RuntimeMobDetails.vue');
const { default: JsonInspector } = await server.ssrLoadModule('/src/components/builder/JsonInspector.vue');
async function render(data, staff = true) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/build/worlds/:world_id/mobs/:mob_definition_id', name: 'builder_mob_definition_details', component: {} },
    { path: '/:pathMatch(.*)*', component: {} },
  ] });
  await router.push('/build/worlds/12/admin/13/mobs/123');
  return renderToString(createSSRApp(Details, { mob: data, path: '/build/worlds/12/admin/13/mobs/123', staff }).use(router));
}
test('mob data separates authored conversation, runtime facts, and staff debug navigation', async () => {
  const html = await render(mob);
  for (const text of ['Menon', 'Agora Northeast Stoa', 'athens-menon', mob.conversation.greeting, mob.conversation.knowledge, 'Vitals and attributes', 'Behavior and services', 'Inventory and equipment', 'Spawn and effects']) assert.ok(html.includes(text), text);
  assert.match(html, /href="\/build\/worlds\/12\/admin\/13\/mobs\/123\/debug"/);
  assert.doesNotMatch(html, /Provider request body|NaN|undefined|A quiet place to test conversations/);
  assert.doesNotMatch(await render(mob, false), /\/debug|DEBUG/);
});
test('definition links use the definition world and ID for both staff and builders', async () => {
  const data = structuredClone(mob);
  data.definition.world_id = 42;
  for (const staff of [true, false]) {
    const html = await render(data, staff);
    assert.match(html, /href="\/build\/worlds\/42\/mobs\/35"[^>]*>athens-menon<\/a>/);
  }
  data.definition.slug = '';
  assert.match(await render(data), /href="\/build\/worlds\/42\/mobs\/35"[^>]*>Menon<\/a>/);
});
test('missing definitions, unconfigured dialogue, pending deletion, and truncation are explicit', async () => {
  const data = structuredClone(mob);
  data.definition = null; data.definition_record = null; data.pending_deletion = true;
  data.conversation.configured = false; data.inventory.next_after = 100; data.inventory.after = 20;
  data.effects_truncated = true; data.factions_truncated = true; data.equipment.truncated = true;
  const html = await render(data);
  assert.match(html, /<dt[^>]*>Definition<\/dt><dd[^>]*>(?:<!--[\s\S]*?-->)*None/);
  assert.doesNotMatch(html, /href="\/build\/worlds\/\d+\/mobs\/\d+"/);
  for (const text of ['pending deletion', 'No LLM conversation entry', 'FIRST 100', 'NEXT 100', 'first 100 effects', 'first 100 faction', 'first 100 equipped']) assert.ok(html.includes(text), text);
});
test('mob text and raw JSON are escaped, with a keyboard-accessible JSON panel', async () => {
  const data = structuredClone(mob); data.runtime.description = '<script>unsafe</script>';
  assert.match(await render(data), /&lt;script&gt;unsafe&lt;\/script&gt;/);
  const html = await renderToString(createSSRApp(JsonInspector, { title: 'Provider request body', value: { text: '<script>unsafe</script>' }, open: true }));
  assert.match(html, /<details[^>]*open/);
  assert.match(html, /<pre[^>]*tabindex="0"/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.match(html, /COPY JSON/);
});
