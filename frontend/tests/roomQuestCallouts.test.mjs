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
});
after(() => server.close());
const { default: Lookup } = await server.ssrLoadModule('/src/components/game/lookup/Lookup.vue');
const { default: LookRoom } = await server.ssrLoadModule('/src/components/game/console/LookRoom.vue');
const notice = { slug: 'defend-athens', text: 'A notice calls for volunteers.', command: 'inspect', indicator: '!' };

test('quest notice lookups render the shared panel and INSPECT on desktop and mobile', async () => {
  for (const is_mobile of [false, true]) {
    const entity = { ...notice, key: 'questnotice.87.defend-athens', text: '<script>notice</script>' };
    const store = createStore({ state: { game: { is_mobile, lookup: { key: entity.key, entity } } } });
    const html = await renderToString(createSSRApp(Lookup).use(store));
    assert.match(html, /lookup-quest-notice/);
    assert.match(html, /Quest notice/);
    assert.match(html, /&lt;script&gt;notice&lt;\/script&gt;/);
    assert.match(html, /<button[^>]*>INSPECT<\/button>/);
    assert.doesNotMatch(html, /lookup-char|Health:|<script>/);
  }
});

test('only the latest room notice exposes a lookup target; keys distinguish rooms', async () => {
  for (const current of [true, false]) {
    for (const id of [87, 88]) {
      const room = { id, name: 'Gate', description: '', chars: [], inventory: [], quest_callouts: [notice] };
      const message = { type: 'cmd.look.success', data: { target: room } };
      const store = createStore({ state: { game: {
        room, player: {}, player_config: {}, last_viewed_room_message: current ? message : null,
      } } });
      const targets = [];
      const app = createSSRApp(LookRoom, {
        message: current ? store.state.game.last_viewed_room_message : message,
      }).use(store).directive('interactive', {
        getSSRProps(binding) { targets.push(binding.value.target); return {}; },
      });
      const html = await renderToString(app);
      assert.match(html, /A notice calls for volunteers\./);
      assert.deepEqual(targets, current ? [{ ...notice, key: `questnotice.${id}.defend-athens` }] : []);
    }
  }
});
