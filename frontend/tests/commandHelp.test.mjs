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
  plugins: [{
    name: 'test-game-router', enforce: 'pre',
    resolveId(id) { if (id.endsWith('/src/router')) return '\0test-game-router'; },
    load(id) { if (id === '\0test-game-router') return 'export default {}'; },
  }],
});
after(() => server.close());
const { default: Help } = await server.ssrLoadModule('/src/components/game/console/Help.vue');
const { default: game } = await server.ssrLoadModule('/src/store/modules/game.ts');

const render = (message, isBuilder = false) => renderToString(createSSRApp(Help, { message })
  .use(createStore({ state: { game: { player: { is_builder: isBuilder } } } })));

test('the menu follows server topics, including every direction and newly added commands', async () => {
  const commands = [
    { command: 'north', commands: ['north', 'east', 'south', 'west', 'up', 'down'], category: 'Movement' },
    { command: 'craft', category: 'Crafting' },
    { command: 'future-command' },
    { command: '/set', category: 'Builder Commands' },
    { command: '/edit', category: 'Builder Commands' },
    { command: 'disengage', category: 'Combat Commands' },
  ];
  const html = await render({ data: { commands } });
  for (const command of ['quit', 'clear', 'north', 'east', 'south', 'west', 'up', 'down',
    'craft', 'future-command', '/set', '/edit', 'disengage']) {
    assert.ok(html.includes(`aria-label="Help for ${command}"`), command);
  }
  assert.equal((html.match(/<button /g) || []).length, 13);
  assert.match(html, /type="button"/);
  assert.doesNotMatch(html, /Help for (?:group|title|focus|\/ban|\/geta|reset)"/);
});

test('a local builder flag cannot add commands absent from the server list', async () => {
  const html = await render({ data: { commands: [{ command: 'look', category: 'Information' }] } }, true);
  assert.match(html, /Help for look/);
  assert.doesNotMatch(html, /Builder Commands|Help for \//);
});

test('command, ability, and plain-text messages do not turn into an unrelated menu', async () => {
  const topic = await render({ data: { command: {
    name: 'Buy', format: 'buy <number|item>', description: 'Buy a listed item.',
    details: ['Use the current stock list.'], examples: ['buy 2'],
  } } });
  assert.match(topic, /buy &lt;number\|item&gt;/);
  assert.match(topic, /Use the current stock list/);
  assert.match(topic, /buy 2/);
  assert.doesNotMatch(topic, /Help for/);
  for (const data of [{ ability: { name: 'Bash' } }, {}]) {
    const html = await render({ data, text: 'First line\nSecond line' });
    assert.match(html, /class="help-text"/);
    assert.match(html, /First line\nSecond line/);
    assert.doesNotMatch(html, /Help for/);
  }
});

test('client command help uses the real command action without disconnecting or clearing output', async () => {
  for (const command of ['help quit', 'h clear', 'HE QUIT', 'hel clear', '  help clear  ']) {
    const sent = [];
    const store = createStore({ modules: { game: {
      ...game,
      state: { ...structuredClone(game.state), player: {}, player_config: { display_chat: true } },
      actions: { ...game.actions, sendWSMessage: (_, payload) => { sent.push(payload); return false; } },
    } } });
    store.commit('game/message_add', { type: 'test.message', text: 'Keep this output' });
    await store.dispatch('game/cmd', command);
    assert.deepEqual(sent, []);
    const messages = store.state.game.messages;
    assert.equal(messages[0].text, 'Keep this output');
    assert.equal(messages[1].text, command);
    assert.equal(messages[2].type, 'cmd.help.success');
    assert.equal(messages[2].data.command.command, command.trim().split(/\s+/)[1].toLowerCase());
    assert.ok(messages.every((message) => !message.command_receipt));
  }
});

test('ordinary command help continues to the server', async () => {
  for (const command of ['help craft', 'help /edit', 'help', 'help quit; look', 'say help clear']) {
    const sent = [];
    const store = createStore({ modules: { game: {
      ...game, state: { ...structuredClone(game.state), player: {} },
      actions: { ...game.actions, sendWSMessage: (_, payload) => { sent.push(payload); return false; } },
    } } });
    await store.dispatch('game/cmd', command);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].text, command);
  }
});
