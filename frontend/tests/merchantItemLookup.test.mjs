import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { createStore } from 'vuex';
import { renderToString } from 'vue/server-renderer';
import { merchantLookupState } from './fixtures/merchantItemLookup.mjs';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { default: Lookup } = await server.ssrLoadModule('/src/components/game/lookup/Lookup.vue');
const { default: List } = await server.ssrLoadModule('/src/components/game/console/List.vue');
const { executeItemAction } = await server.ssrLoadModule('/src/core/itemActions.ts');

test('finite and unlimited shop lookups show equipment details and preserve the exact purchase target', async () => {
  for (const is_mobile of [false, true]) {
    for (const unlimited of [false, true]) {
      const game = merchantLookupState(is_mobile);
      const message = game.last_message['cmd.list.success'];
      const entry = message.data.stock[0];
      entry.unlimited = unlimited;
      if (!unlimited) entry.item.key = 'item.8';
      const store = createStore({ state: { game } });
      const targets = [];
      await renderToString(createSSRApp(List, {
        message: store.state.game.last_message[message.type],
      }).use(store).directive('interactive', {
        getSSRProps(binding) { targets.push(binding.value.target); return {}; },
      }));
      assert.equal(targets.length, 1);
      const entity = targets[0];
      store.state.game.lookup = { key: entity.key, entity };
      const html = await renderToString(createSSRApp(Lookup).use(store));
      assert.match(html, /lookup-item/);
      assert.match(html, /Level 20 heavy armor, worn on head/);
      assert.match(html, /sun-faded horsehair crest/);
      assert.match(html, /Constitution/);
      assert.match(html, /Strength/);
      assert.match(html, /Armor/);
      assert.match(html, />168<\/td>/);
      assert.match(html, /\(\+68\)/);
      assert.match(html, /\(\+2\)/);
      assert.match(html, /Value: 50 OBOLS/);
      assert.match(html, /BUY FOR 75 OBOLS/);
      assert.doesNotMatch(html, /lookup-char|Health:|>WEAR<|>REMOVE</);
      const commands = [];
      executeItemAction({ dispatch: (...args) => commands.push(args), commit() {} }, entity, 'buy');
      assert.deepEqual(commands, [['game/cmd', 'buy merchant_stock_entry.7 from room.158']]);
    }
  }
});

test('catalog containers receive item normalization before rendering', async () => {
  const game = merchantLookupState();
  const entity = { key: 'merchant_stock_entry.9', name: 'a satchel', type: 'container', actions: ['buy'] };
  game.lookup = { key: entity.key, entity };
  const html = await renderToString(createSSRApp(Lookup).use(createStore({ state: { game } })));
  assert.match(html, /lookup-item/);
  assert.match(html, /Container is empty/);
  assert.match(html, /It is a satchel/);
  assert.doesNotMatch(html, /Health:/);
});

test('equipped items retain their lookup and mobs retain character health', async () => {
  const game = merchantLookupState();
  game.lookup = { key: game.player.equipment.head.key };
  const store = createStore({ state: { game } });
  const itemHtml = await renderToString(createSSRApp(Lookup).use(store));
  assert.match(itemHtml, /lookup-item/);
  assert.match(itemHtml, /REMOVE/);
  assert.doesNotMatch(itemHtml, /Health:/);
  const mob = { key: 'mob.2', name: 'an armorer', level: 20, health: 80, health_max: 100 };
  store.state.game.lookup = { key: mob.key, entity: mob };
  const mobHtml = await renderToString(createSSRApp(Lookup).use(store));
  assert.match(mobHtml, /lookup-char/);
  assert.match(mobHtml, /Health: 80 \/ 100/);
});
