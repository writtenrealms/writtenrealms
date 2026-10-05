import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createSSRApp } from "vue";
import { createStore } from "vuex";
import { renderToString } from "vue/server-renderer";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  plugins: [{
    name: "test-game-router",
    enforce: "pre",
    resolveId(id) {
      if (id.endsWith("/src/router")) return "\0test-game-router";
    },
    load(id) {
      if (id === "\0test-game-router") return "export default {}";
    },
  }],
});
after(() => server.close());
const { default: Chat } = await server.ssrLoadModule("/src/components/game/console/Chat.vue");
const { default: Console } = await server.ssrLoadModule("/src/components/game/console/Console.vue");
const { default: ComLog } = await server.ssrLoadModule("/src/components/game/sidebar/ComLog.vue");
const { default: AccessibleGame } = await server.ssrLoadModule("/src/components/game/AccessibleGame.vue");
const { default: game } = await server.ssrLoadModule("/src/store/modules/game.ts");
const message = (type, text, data = {}) => ({ type, text, data });
const renderChat = (value) => renderToString(createSSRApp(Chat, { message: value }).use(createStore({})));

test("only incoming questions show a single spaced ID, including multiline messages", async () => {
  const incoming = await renderChat(message("notification.cmd.ask.success",
    "Ada asks 'Where is the inn?\nI am near the gate.' [ 3 ]", { question_id: 3 }));
  assert.equal(incoming.match(/\[ 3 \]/g)?.length, 1);
  assert.match(incoming, /<button[^>]*type="button"[^>]*class="question-id"[^>]*aria-label="Answer question 3"[^>]*>\[ 3 \]<\/button>/);
  assert.match(incoming, /gate\.&#39;.*class="question-id"/);
  for (const command of ["ask", "answer"]) {
    for (const prefix of ["cmd", "notification.cmd"]) {
      if (command === "ask" && prefix === "notification.cmd") continue;
      const html = await renderChat(message(`${prefix}.${command}.success`,
        "A message without an ID.", { question_id: 3 }));
      assert.doesNotMatch(html, /class="question-id"|\[ 3 \]/);
    }
  }
});

test("question buttons prepare the selected reply and close the log without sending", async () => {
  for (const isOpen of [false, true]) {
    const commands = [];
    const store = createStore({
      modules: { game: { namespaced: true,
        state: { command_draft: null }, mutations: game.mutations,
        actions: { cmd: (_, text) => commands.push(text) },
      } },
      state: { ui: { modal: { isOpen } } },
      mutations: { 'ui/modal/close': state => { state.ui.modal.isOpen = false; } },
    });
    let bindings;
    await renderToString(createSSRApp({ setup() {
      bindings = Chat.setup({ message: message('notification.cmd.ask.success',
        "Mira asks 'Where?' [ 3 ]", { question_id: 3 }) }, { expose() {} });
      return () => null;
    } }).use(store));
    bindings.prepareAnswer();
    assert.deepEqual(store.state.game.command_draft, { text: 'answer 3 ' });
    assert.equal(store.state.ui.modal.isOpen, false);
    const previous = store.state.game.command_draft;
    bindings.prepareAnswer();
    assert.notEqual(store.state.game.command_draft, previous);
    assert.deepEqual(commands, []);
  }
});

test("accessible console exposes the same keyboard-accessible question button", async () => {
  const store = createStore({ state: { game: {
    messages: [message('notification.cmd.ask.success', "Mira asks 'Where?' [ 3 ]", { question_id: 3 })],
  } } });
  const html = await renderToString(createSSRApp(AccessibleGame).use(store));
  assert.match(html, /<button[^>]*aria-label="Answer question 3"/);
  assert.match(html, /id="console-input"/);
});

test("only matching metadata is styled; body text resembling IDs is preserved", async () => {
  const html = await renderChat(message("notification.cmd.ask.success", "Ada asks 'What does [ 3 ] mean?' [ 3 ]", { question_id: 3 }));
  assert.equal(html.match(/\[ 3 \]/g)?.length, 2);
  for (const id of [undefined, "3", 0, -3, true, Number.MAX_SAFE_INTEGER + 1]) {
    const fallback = await renderChat(message("notification.cmd.ask.success", "Ada asks 'Hello' [ 3 ]", { question_id: id }));
    assert.doesNotMatch(fallback, /class="question-id"/);
    assert.match(fallback, /\[ 3 \]/);
  }
  const chat = await renderChat(message("cmd.chat.success", "You chat '[3]'", { question_id: 3 }));
  assert.doesNotMatch(chat, /class="question-id"/);
});

test("player markup is escaped while safe web links remain clickable", async () => {
  const html = await renderChat(message("notification.cmd.chat.success",
    'Ada chats \'<img src=x onerror=alert(1)> <script>alert(1)</script> https://example.com/?a=1&b=2 <a href="javascript:alert(1)">click</a>\''));
  assert.doesNotMatch(html, /<img|<script|href="javascript:/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /href="https:\/\/example.com\/\?a=1&amp;b=2"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.equal(html.match(/<a /g)?.length, 1);
});

test("URL quotes cannot inject link attributes and punctuation remains visible", async () => {
  const html = await renderChat(message("cmd.gossip.success",
    'https://example.com/" onmouseover="alert(1)" & https://example.org/path.'));
  assert.doesNotMatch(html, /<a[^>]*onmouseover/);
  assert.match(html, /&quot; onmouseover=&quot;alert\(1\)&quot;/);
  assert.match(html, /href="https:\/\/example.org\/path"/);
  assert.match(html, /https:\/\/example.org\/path<\/a>.*\./);
});

test("builder labels support the current actor payload and absent metadata", async () => {
  const html = await renderChat(message("notification.cmd.answer.success", "Ada answers Mira 'East.'", {
    actor: { name: "Ada", is_builder: true }, question_id: 1,
  }));
  assert.match(html, /\[ Builder \]/);
  assert.match(html, /Ada answers Mira/);
  assert.doesNotMatch(await renderChat({ type: "cmd.chat.success", text: "You chat 'Hello.'" }), /\[ Builder \]/);
});

test("answers preserve each viewer's wording in the console and communication log", async () => {
  for (const [type, text] of [
    ['cmd.answer.success', "You answer 'At the harbor shop.'"],
    ['notification.cmd.answer.success', "Alden answers you 'At the harbor shop.'"],
    ['notification.cmd.answer.success', "Alden answers Mira 'At the harbor shop.'"],
  ]) {
    const entry = { ...message(type, text, { question_id: 3, target: { id: 2, name: 'Mira' } }), message_id: 1 };
    const store = createStore({
      state: { game: { is_mobile: false, com_list: [entry] } },
      getters: { 'game/consoleMessages': () => [entry] },
    });
    for (const component of [Console, ComLog]) {
      const html = await renderToString(createSSRApp(component).use(store));
      assert.ok(html.includes(text.replaceAll("'", '&#39;')));
      assert.doesNotMatch(html, /class="question-id"/);
    }
  }
});

test("console routes ask and answer through the ID renderer and private messages through escaped text", async () => {
  const messages = [
    message("notification.cmd.ask.success", "Ada asks 'Where?' [ 2 ]", { question_id: 2 }),
    message("cmd.answer.success", "You answer 'North.'", { question_id: 2 }),
    message("notification.cmd.tell.success", "Ada tells you '<img src=x>'"),
    message("cmd.whisper.success", "You whisper to Ada '<script>hello</script>'"),
  ].map((value, message_id) => ({ ...value, message_id }));
  const html = await renderToString(createSSRApp(Console).use(createStore({
    state: { game: { is_mobile: false } },
    getters: { "game/consoleMessages": () => messages },
  })));
  assert.equal(html.match(/class="question-id"/g)?.length, 1);
  assert.match(html, /&lt;img src=x&gt;/);
  assert.match(html, /&lt;script&gt;hello&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<img|<script/);
});

const gameHarness = async () => {
  let receive;
  const store = createStore({ modules: { game: {
    ...game,
    state: { ...structuredClone(game.state), player_config: { display_chat: false } },
    mutations: { ...game.mutations, openWS: (_state, callbacks) => { receive = callbacks.onmessage; } },
  } } });
  await store.dispatch("game/openWebSocket");
  return { store, receive: value => receive({ data: JSON.stringify(value) }) };
};

test("received channel and private messages reach history once, independent of display-chat preference", async (t) => {
  t.mock.method(console, "log", () => {});
  const { store, receive } = await gameHarness();
  const types = ["ask", "answer", "chat", "gossip", "tell", "whisper", "cchat"]
    .flatMap(command => [`cmd.${command}.success`, `notification.cmd.${command}.success`]);
  for (const type of types) {
    const value = message(type, type, { _event_id: type });
    receive(value);
    receive(value);
  }
  receive(message("cmd.listen.success", "You listen to ask."));
  assert.deepEqual(store.state.game.com_list.map(entry => entry.type), types);
  const visible = store.getters["game/consoleMessages"].map(entry => entry.type);
  assert.ok(!visible.includes("notification.cmd.chat.success"));
  for (const command of ["ask", "answer", "gossip", "tell", "whisper"]) {
    assert.ok(visible.includes(`notification.cmd.${command}.success`));
  }
});

test("communication history retains only the newest 200 immutable snapshots", async () => {
  const { store } = await gameHarness();
  for (let index = 0; index < 450; index += 1) {
    const value = message("cmd.ask.success", `Question ${index}`, { question_id: index + 1 });
    store.commit("game/com_list_add", value);
    value.text = "changed";
    value.data.question_id = -1;
  }
  assert.equal(store.state.game.com_list.length, 200);
  assert.equal(store.state.game.com_list[0].text, "Question 250");
  assert.equal(store.state.game.com_list[199].data.question_id, 450);
});

test("communication log renders newest messages first with escaped text and question IDs", async () => {
  const { store } = await gameHarness();
  store.commit("game/com_list_add", message("cmd.ask.success", "You ask '<img src=x>'", { question_id: 7 }));
  store.commit("game/com_list_add", message("notification.cmd.answer.success", "Ada answers 'Hello.'", { question_id: 7 }));
  store.commit("game/com_list_add", message("notification.cmd.ask.success", "Ada asks 'Where?' [ 8 ]", { question_id: 8 }));
  const html = await renderToString(createSSRApp(ComLog).use(store));
  assert.equal(html.match(/class="question-id"/g)?.length, 1);
  assert.match(html, /\[ 8 \]/);
  assert.doesNotMatch(html, /\[ 7 \]/);
  assert.ok(html.indexOf("Ada asks") < html.indexOf("Ada answers"));
  assert.ok(html.indexOf("Ada answers") < html.indexOf("You ask"));
  assert.match(html, /&lt;img src=x&gt;/);
  assert.doesNotMatch(html, /<img/);
});

test("empty communication log introduces ask and channel discovery", async () => {
  const { store } = await gameHarness();
  const html = await renderToString(createSSRApp(ComLog).use(store));
  assert.match(html, /Use &#39;ask&#39; to ask a question/);
  assert.match(html, /&#39;listen&#39; to see available channels/);
});
