// Open on Vite at mobile and desktop widths. Uses real console components and
// CSS; commands are captured locally, never sent to the backend.
import { createApp, h, nextTick } from 'vue';
import store from '../../src/store';
import { interactive } from '../../src/core/directives';
import Console from '../../src/components/game/console/Console.vue';
import Lookup from '../../src/components/game/lookup/Lookup.vue';
import Modal from '../../src/components/ui/Modal.vue';
import '../../src/styles/app.scss';

const room = {
  id: 87, key: 'room.87', manifest_ref: 'room@87', name: 'Before the Thriasian Gate',
  description: 'A broad gate court spreads before the Thriasian Gate, its packed surface cut by wheel ruts and edged with pale stone curbs. The gate opens west through the city wall, a guard tower rises to the north, and streets lead south to the Panathenaic Way and east toward the workshops.',
  chars: [], inventory: [], details: [], actions: [], north: 89, east: 113, south: 86, west: 88,
  quest_callouts: [{ slug: 'athens-persians',
    text: 'A muster notice beside the gate calls for volunteers to strike the Persian outpost northwest of Athens.',
    indicator: '!', command: 'inspect' }],
};
const look = { type: 'cmd.look.success', message_id: 'look', data: { target_type: 'room', target: room } };
const commands: string[] = [];
// The directive uses the app's store singleton. Replace game commands before
// mounting so every interaction in this fixture stays local.
store.hotUpdate({ modules: { game: {
  namespaced: true,
  getters: { consoleMessages: state => state.messages },
  actions: { cmd: (_context, command: string) => { commands.push(command); } },
} } });
Object.assign(store.state.game, {
    messages: [{ type: 'cmd.text', text: 'look', echo: true, message_id: 'echo' }, look],
    last_message: {}, last_viewed_room_message: look,
    world: { id: 12 }, room, player: { id: 1, is_builder: true },
    player_id: 1, player_config: {}, is_mobile: innerWidth <= 768,
});
createApp({ render: () => h('div', [
  h(Console),
  store.state.game.lookup && !store.state.game.is_mobile ? h('div', {
    class: 'fixture-lookup',
    style: {
      position: 'fixed', left: `${store.state.game.lookup.position.left + 50}px`,
      bottom: `${innerHeight - store.state.game.lookup.position.top}px`,
    },
    onMouseover: () => store.commit('game/popup_hover_set', true),
    onMouseleave: () => {
      store.commit('game/popup_hover_set', false);
      store.commit('game/lookup_clear');
    },
  }, [h(Lookup)]) : null,
  store.state.ui.modal.isOpen ? h(Modal) : null,
]) }).use(store).directive('interactive', interactive).mount('#app');

const frame = async () => { await nextTick(); await new Promise(requestAnimationFrame); };
const debounce = () => new Promise(resolve => setTimeout(resolve, 200));
const enter = (element: HTMLElement) => element.dispatchEvent(new MouseEvent('mouseenter'));
const leave = (element: HTMLElement) => element.dispatchEvent(new MouseEvent('mouseleave'));
let checks = 0;
const check = (condition: boolean, message: string) => {
  if (!condition) throw new Error(message);
  checks += 1;
};
async function run() {
  await document.fonts.ready;
  await frame();
  const callout = document.querySelector<HTMLElement>('.room-quest-callout')!;
  const text = callout.querySelector<HTMLElement>('.interactive')!;
  const indicator = callout.querySelector<HTMLElement>('.quest-indicator-wrapper')!;
  for (const marker of ['!', '?', '']) {
    store.state.game.room.quest_callouts[0].indicator = marker;
    await frame();
    const range = document.createRange();
    range.selectNodeContents(indicator);
    const lines = new Set(Array.from(range.getClientRects()).map(rect => Math.round(rect.top)));
    check(lines.size === 1, `Quest marker wraps at ${innerWidth}px`);
    check(indicator.textContent?.trim() === `[ ${marker || '!'} ]`, 'Marker text changed');
  }
  store.state.game.room.quest_callouts[0].indicator = '!';
  const textRange = document.createRange();
  textRange.selectNodeContents(text);
  const textLines = new Set(Array.from(textRange.getClientRects()).map(rect => Math.round(rect.top)));
  const underlineFragments = new Set(Array.from(text.getClientRects()).map(rect => Math.round(rect.top)));
  check(underlineFragments.size === textLines.size, 'Underline does not follow every text line');
  check(getComputedStyle(text).borderBottomStyle === 'dotted', 'Existing dotted underline was lost');
  if (innerWidth <= 430) check(textLines.size > 1, 'Mobile fixture did not wrap');
  check(indicator.getBoundingClientRect().right <= callout.getBoundingClientRect().right + 1,
    'Quest marker overflows the room');
  enter(text);
  await debounce();
  await frame();
  if (store.state.game.is_mobile) {
    check(!store.state.game.lookup, 'Mobile hover opened a popup before the tap');
    text.click();
    await frame();
    check(!!document.querySelector('#modal .lookup-quest-notice'), 'Tap did not open the shared lookup modal');
  } else {
    check(!!document.querySelector('.fixture-lookup .lookup-quest-notice'), 'Hover did not open the shared lookup');
    // Moving from the notice into its popup must leave INSPECT reachable.
    document.querySelector('.fixture-lookup')!.dispatchEvent(new MouseEvent('mouseover'));
    leave(text);
    await debounce();
    await frame();
    check(!!document.querySelector('.lookup-quest-notice'), 'Popup closed while entering its action row');
  }
  check(commands.length === 0, 'Opening the preview sent a game command');
  check(document.querySelector('.lookup-quest-notice .description')?.textContent === room.quest_callouts[0].text,
    'Lookup did not display the authored notice');
  document.querySelector<HTMLButtonElement>('.lookup-quest-notice button')!.click();
  await frame();
  check(commands.length === 1 && commands[0] === 'inspect', 'INSPECT did not send exactly one command');
  check(!store.state.game.lookup && !store.state.ui.modal.isOpen, 'INSPECT left the lookup open');
  store.commit('game/popup_hover_set', false);
  if (!store.state.game.is_mobile) {
    text.click();
    check(commands.length === 2 && commands[1] === 'inspect', 'Desktop direct click no longer inspects');
  }
  const commandCount = commands.length;
  store.state.game.last_viewed_room_message = null;
  await frame();
  check(!callout.querySelector('.interactive'), 'Historical notice remained interactive');
  callout.querySelector<HTMLElement>('span')!.click();
  check(commands.length === commandCount, 'Historical notice sent a command');
  enter(callout.querySelector<HTMLElement>('span')!);
  await debounce();
  check(!store.state.game.lookup, 'Historical notice opened a preview');
  store.state.game.last_viewed_room_message = store.state.game.messages[1];
  await frame();
  document.querySelector('#results')!.textContent = `PASS: ${checks} checks at ${innerWidth}px`;
  // Leave a real lookup open for screenshots and manual review when requested.
  if (new URLSearchParams(location.search).has('preview')) {
    const currentText = callout.querySelector<HTMLElement>('.interactive')!;
    if (store.state.game.is_mobile) currentText.click();
    else enter(currentText);
  }
}
run().catch(error => {
  document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
  console.error(error);
});
