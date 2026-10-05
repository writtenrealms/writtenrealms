// A real Console with local fixtures: commands never reach the server.
import { createApp, h, nextTick } from 'vue';
import { createStore } from 'vuex';
// Match the app entry point's router/store initialization order.
import '../../src/router';
import Console from '../../src/components/game/console/Console.vue';
import Input from '../../src/components/game/Input.vue';
import ComLog from '../../src/components/game/sidebar/ComLog.vue';
import Modal from '../../src/components/ui/Modal.vue';
import game from '../../src/store/modules/game';
import modal from '../../src/store/modules/ui/modal';
import '../../src/styles/app.scss';

const echo = (text: string) => ({ type: 'cmd.text', text, echo: true, data: {} });
const messages = [
  echo('listen'),
  { type: 'cmd.listen.success', text: 'You are currently listening to: ask.\nAvailable channels: ask, chat, gossip.\nUse listen <channel> [on|off] to join or leave.', data: {} },
  echo('ask Where can I find a healer?'),
  { type: 'cmd.ask.success', text: "You ask 'Where can I find a healer?'", data: { question_id: 3 } },
  { type: 'notification.cmd.ask.success', text: "Mira asks 'How do I see my quests?' [ 4 ]", data: { question_id: 4 } },
  { type: 'notification.cmd.answer.success', text: "Alden answers you 'The healer is just east of the market square.'", data: { question_id: 3, is_builder: true } },
  echo('answer 4 Type quest log to see your current quests.'),
  { type: 'cmd.answer.success', text: "You answer 'Type quest log to see your current quests.'", data: { question_id: 4 } },
  { type: 'notification.cmd.answer.success', text: "Alden answers Mira 'The quest log also shows completed quests.'", data: { question_id: 4, is_builder: true } },
  echo('listen ask off'),
  { type: 'cmd.listen.success', text: 'You no longer listen to the ask channel.', data: {} },
  echo('ask Can I still ask for help?'),
  { type: 'cmd.ask.success', text: "You ask 'Can I still ask for help?'", data: { question_id: 5 } },
  { type: 'notification.cmd.answer.success', text: "Alden answers you 'Yes. You still receive replies to your own questions.'", data: { question_id: 5, is_builder: true } },
  echo('listen chat'),
  { type: 'cmd.listen.success', text: 'You now listen to the chat channel.', data: {} },
  { type: 'notification.cmd.chat.success', text: "Mira chats 'Anyone heading to the ruins?'", data: {} },
  { type: 'notification.cmd.tell.success', text: "Mira tells you 'Thanks for the help!'", data: {} },
  echo('whisper Mira You are welcome.'),
  { type: 'cmd.whisper.success', text: "You whisper to Mira 'You are welcome.'", data: {} },
  { type: 'notification.cmd.gossip.success', text: "Alden gossips 'Today feels like a good day for an adventure.'", data: {} },
  { type: 'notification.cmd.chat.success', text: "Mira chats 'The guide is at https://writtenrealms.com/docs — and <b>this is plain text</b>.'", data: {} },
].map((message, message_id) => ({ ...message, message_id }));

const sentCommands: string[] = [];
const store = createStore({ modules: {
  game: {
    namespaced: true,
    state: { messages, player: {}, is_mobile: false, command_draft: null, com_list: messages.filter(
      message => message.type === 'notification.cmd.ask.success',
    ) },
    getters: { consoleMessages: state => state.messages },
    mutations: game.mutations,
    actions: { cmd: (_, text) => { sentCommands.push(text); } },
  },
  ui: { namespaced: true, modules: { modal } },
} });
createApp({ render() {
  return h('div', { class: 'communication-test' }, [
    h(Console), h(Input),
    h('button', { type: 'button', onClick: () => store.commit('ui/modal/open_view', { component: ComLog }) }, 'Open communication log'),
    store.state.ui.modal.isOpen ? h(Modal) : null,
  ]);
} }).use(store).mount('#app');

async function check() {
  await nextTick();
  await new Promise(requestAnimationFrame);
  const ids = Array.from(document.querySelectorAll<HTMLElement>('.question-id'));
  if (ids.map(id => id.textContent?.trim()).join(',') !== '[ 4 ]') {
    throw new Error('Only incoming questions should show spaced IDs');
  }
  if (ids.some(id => getComputedStyle(id).color !== 'rgb(153, 153, 153)')) {
    throw new Error('Question IDs are not gray');
  }
  if (document.querySelector('.line b')) throw new Error('Player markup became HTML');
  const link = document.querySelector<HTMLAnchorElement>('.line a');
  if (link?.getAttribute('href') !== 'https://writtenrealms.com/docs') {
    throw new Error('Web link was not preserved');
  }
  const luminance = (rgb: number[]) => rgb.map(value => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }).reduce((value, channel, index) => value + channel * [0.2126, 0.7152, 0.0722][index], 0);
  const background = luminance([25, 26, 28]);
  const privateAndGossipMessages = Array.from(document.querySelectorAll<HTMLElement>('.message'))
    .filter(element => /(?:tell|whisper|gossip)\.success/.test(element.className));
  const contrasts = privateAndGossipMessages.map(element => {
    const rgb = getComputedStyle(element).color.match(/\d+/g)!.slice(0, 3).map(Number);
    return (luminance(rgb) + 0.05) / (background + 0.05);
  });
  if (contrasts.length !== 3 || contrasts.some(contrast => contrast < 4.5)) {
    throw new Error('Private messages or gossip have insufficient text contrast');
  }
  const input = document.querySelector<HTMLInputElement>('#console-input')!;
  ids[0].click();
  await nextTick();
  await nextTick();
  if (input.value !== 'answer 4 ' || document.activeElement !== input || input.selectionStart !== 9) {
    throw new Error('Reply button did not prepare and focus the input at the end');
  }
  store.commit('ui/modal/open_view', { component: ComLog });
  await nextTick();
  document.querySelector<HTMLButtonElement>('#coms_log .question-id')!.click();
  await nextTick();
  await nextTick();
  if (store.state.ui.modal.isOpen || input.value !== 'answer 4 ' || document.activeElement !== input) {
    throw new Error('Replying from the log did not close it and focus the command input');
  }
  if (sentCommands.length) throw new Error('Preparing a reply sent a command');
  const answerLines = Array.from(document.querySelectorAll<HTMLElement>('.line')).map(line => line.textContent);
  for (const prefix of ["You answer '", "Alden answers you '", "Alden answers Mira '"]) {
    if (!answerLines.some(line => line?.startsWith(prefix))) throw new Error(`Missing answer perspective: ${prefix}`);
  }
  document.querySelector('#results')!.textContent = 'PASS: three answer perspectives, reply buttons, input focus, log replies, and safe rendering';
}
check().catch(error => {
  document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
  console.error(error);
});
