import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp, defineComponent, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createStore } from 'vuex';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { useBuilding } = await server.ssrLoadModule('/src/composables/useBuilding.ts');
const { platformConfig } = await server.ssrLoadModule('/src/core/platform.ts');

const canBuild = async (user, buildingEnabled) => {
  platformConfig.value = { main_world_id: 1, building_enabled: buildingEnabled };
  const store = createStore({
    state: { auth: { user } },
    getters: { isAuthenticated: () => !!user.id },
  });
  const Probe = defineComponent({
    setup() {
      const { canCreateWorlds } = useBuilding();
      return () => h('span', String(canCreateWorlds.value));
    },
  });
  const html = await renderToString(createSSRApp(Probe).use(store));
  return html === '<span>true</span>';
};

test('staff can always build; others follow the site setting', async () => {
  assert.equal(await canBuild({ id: 1, is_staff: true }, false), true);
  assert.equal(await canBuild({ id: 2, is_staff: false }, false), false);
  assert.equal(await canBuild({ id: 2, is_staff: false }, true), true);
});

test('signed-out and temporary users cannot build', async () => {
  assert.equal(await canBuild({}, true), false);
  assert.equal(await canBuild({ id: 3, is_temporary: true }, true), false);
});
