// Open /tests/browser/consoleScroll.html on Vite. Exercises the real console,
// Talk lookup, and history eviction mutation. All command replies are local.
import { createApp, h, nextTick, ref } from 'vue';
import { createStore } from 'vuex';
import Console from '../../src/components/game/console/Console.vue';
import LookupChar from '../../src/components/game/lookup/LookupChar.vue';
import gameStore from '../../src/store';
import '../../src/styles/app.scss';

const healer = {
  key: 'mob.1', char_type: 'mob', name: 'a gray-haired healer',
  keyword: 'healer', keywords: 'healer',
  description: 'Gray curls escape the strip of linen holding this woman’s hair clear of her face.',
};
const paragraph = 'Rows of low wooden couches fill a limewashed chamber, each laid with folded wool and clean linen beneath shelves of stoppered clay jars. Bronze probes, bandage rolls, and shallow mixing bowls stand in orderly groups on a long stone table.';
const popup = ref(false);
const commands: string[] = [];
Object.assign(gameStore.state.game, {
  messages: [], last_message: {}, is_mobile: false,
  world: { allow_combat: false }, room: { chars: [healer] }, player: { key: 'player.1' },
});
const store = createStore({
  state: { game: gameStore.state.game },
  getters: { 'game/consoleMessages': state => state.game.messages },
  mutations: {
    'game/message_add': (_state, message) => gameStore.commit('game/message_add', message),
    'game/lookup_clear': () => { popup.value = false; },
    'ui/modal/close': () => {},
  },
  actions: { 'game/cmd': ({ commit }, command: string) => {
    commands.push(command);
    commit('game/message_add', { type: 'cmd.text', text: command, echo: true });
  } },
});
const add = (message: any) => store.commit('game/message_add', message);
for (let i = 0; i < 200; i++) {
  add({ type: 'write.room', text: `${i + 1}. The Iatreion\n${paragraph}\n${paragraph}` });
}
createApp({ setup: () => () => h('div', [
  h(Console),
  popup.value ? h('aside', { class: 'test-lookup' }, [h(LookupChar, { entity: healer })]) : null,
]) }).use(store).directive('interactive', {}).mount('#app');

const settle = async () => {
  await nextTick();
  // Native scroll events, ResizeObserver and the coalesced follow span frames.
  for (let i = 0; i < 3; i++) await new Promise(requestAnimationFrame);
};
let assertions = 0;
function check(condition: boolean, message: string) {
  if (!condition) throw new Error(message);
  assertions += 1;
}
async function run() {
  await settle();
  const viewport = document.querySelector<HTMLElement>('#console')!;
  const atBottom = () => Math.abs(viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop) <= 4;
  const checkFollowing = () => {
    check(atBottom(), 'New output was hidden below the viewport');
    check(!document.querySelector('.scroll-tool-region'), 'Unexpected Jump to Bottom callout');
    check(store.state.game.messages.length === 200, 'History was not kept bounded');
  };
  checkFollowing();
  for (let i = 0; i < 3; i++) {
    popup.value = true;
    await settle();
    document.querySelector<HTMLElement>('.test-lookup .action.primary')!.click();
    check(commands.pop() === 'talk healer', 'Lookup did not dispatch Talk');
    await settle();
    checkFollowing();
    add({ type: 'cmd.talk.success', text: 'You talk to a gray-haired healer.' });
    add({ type: 'quest.interaction.hint', text: 'Bring clean bandages to the stone table.', data: {
      target: { name: 'A gray-haired healer' },
      hint: 'Bring clean bandages to the stone table.\nThe healer’s full reply should be visible.',
    } });
    await settle();
    checkFollowing();
  }
  viewport.scrollTop -= 180;
  await settle();
  check(!!document.querySelector('.scroll-tool-region'), 'Deliberate scrollback did not pause following');
  check(getComputedStyle(viewport).overflowAnchor === 'auto', 'Native anchoring was not restored for scrollback');
  const retained = viewport.querySelector<HTMLElement>('.message:nth-last-child(5)')!;
  const previousTop = retained.getBoundingClientRect().top;
  add({ type: 'write.room', text: paragraph });
  await settle();
  check(!atBottom(), 'Incoming output pulled the reader away from scrollback');
  check(Math.abs(retained.getBoundingClientRect().top - previousTop) <= 1, 'History eviction shifted the reader’s text');
  document.querySelector<HTMLElement>('.scroll-tool-view')!.click();
  await settle();
  checkFollowing();
  check(getComputedStyle(viewport).overflowAnchor === 'none', 'Bottom following did not disable native anchoring');
  add({ type: 'write.room', text: 'The healer returns to sorting bandages.' });
  await settle();
  checkFollowing();
  document.querySelector('#results')!.textContent = `PASS: ${assertions} checks — Talk replies follow at the 200-message limit; scrollback stays put.`;
}
run().catch(error => {
  document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
  console.error(error);
});
