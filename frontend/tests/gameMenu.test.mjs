import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { createStore } from 'vuex';
import { renderToString } from 'vue/server-renderer';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const menus = await Promise.all(['desktop', 'mobile'].map(async layout => {
  const { default: Menu } = await server.ssrLoadModule(`/src/components/game/${layout}/Menu.vue`);
  return [layout, Menu];
}));

const render = (component, instanceOfId, isTemporary = false) => renderToString(
  createSSRApp(component).use(createStore({
    state: {
      auth: { user: { is_temporary: isTemporary } },
      game: { world: { id: 12, instance_of_id: instanceOfId } },
    },
  })),
);

for (const [layout, Menu] of menus) {
  test(`${layout} instance menu offers Exit Game immediately after Leave Instance`, async () => {
    const html = await render(Menu, 2);
    const labels = [...html.matchAll(/<a\b[^>]*>([^<]+)<\/a>/g)].map(([, label]) => label);
    assert.deepEqual(labels.slice(-2), ['Leave Instance', 'Exit Game']);
    assert.equal(labels.filter(label => label === 'Exit Game').length, 1);
  });

  test(`${layout} base-world menu offers Exit Game without Leave Instance`, async () => {
    const html = await render(Menu, null);
    assert.match(html, />Exit Game<\/a>/);
    assert.doesNotMatch(html, /Leave Instance/);
  });

  if (layout === 'desktop') {
    test('temporary characters retain the introduction menu', async () => {
      const html = await render(Menu, 2, true);
      assert.match(html, /Save Character/);
      assert.match(html, /Exit Introduction/);
      assert.doesNotMatch(html, /Leave Instance|Exit Game/);
    });
  }
}
