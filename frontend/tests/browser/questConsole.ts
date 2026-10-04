// Open /tests/browser/questConsole.html on Vite to exercise actual console
// layout and reactive work buttons. Commands are captured locally, never sent.
import { createApp, nextTick } from 'vue';
import { createStore } from 'vuex';
import Console from '../../src/components/game/console/Console.vue';
import '../../src/styles/app.scss';

const room = {
  id: 114, key: 'room.114', manifest_ref: 'room@114', name: 'A Clay-Washing Yard',
  description: 'A muddy clay-washing yard spreads beside the Eridanos bank, with settling basins cut into the ground and reed baskets stacked near water jars.',
  chars: [], inventory: [], details: [], actions: ['wash clay'], north: 1, east: 2,
};
const quest = {
  id: 33, status: 'active', template: { name: 'Clay for the Wheel', slug: 'athens-clay-for-the-wheel' },
  current_step: {
    text: { body: '"The wheel wants clean clay, not pebbles," the clay washer says. "Wash a basket, strain it, and take it to the potter in the Kiln Court. He\'ll put it aside for tomorrow."\n\nHe sets a basket of rough clay beside the settling basin.' },
    recap: 'WASH CLAY in the settling basin.',
    room_action: { command: 'wash clay', room_key: room.key, world_id: 24 },
  },
};
const echo = (text: string) => ({ type: 'cmd.text', text, echo: true });
const info = { type: 'cmd.quest.success', data: { subcommand: 'info', quest } };
const look = { type: 'cmd.look.success', data: { target_type: 'room', target: room } };
const messages = [
  echo('l'), look,
  echo('talk claywasher'), { type: 'cmd.talk.success', text: 'You talk to a clay washer.' },
  echo('quest accept athens-clay-for-the-wheel'), { type: 'quest.instance.started', data: { quest } },
  echo('quest info athens-clay-for-the-wheel'), info,
].map((message, message_id) => ({ ...message, message_id }));
const commands: string[] = [];
const store = createStore({
  state: { game: {
    messages, last_message: {}, last_viewed_room_message: null,
    world: { id: 24 }, room, player: { id: 16, is_builder: true },
    player_id: 16, player_config: {}, is_mobile: false,
  } as any },
  getters: { 'game/consoleMessages': state => state.game.messages },
  actions: { 'game/cmd': (_context, command: string) => { commands.push(command); } },
});
store.state.game.last_message[info.type] = store.state.game.messages[7];
store.state.game.last_viewed_room_message = store.state.game.messages[1];
createApp(Console).use(store).directive('interactive', {}).mount('#app');

const frame = async () => { await nextTick(); await new Promise(requestAnimationFrame); };
const workButton = () => document.querySelector<HTMLButtonElement>('.quest-actions .primary');
let assertions = 0;
function check(condition: boolean, message: string) {
  if (!condition) throw new Error(message);
  assertions += 1;
}
async function run() {
  await frame();
  const replies = Array.from(document.querySelectorAll<HTMLElement>('.command-response'));
  const gaps = replies.map(reply => reply.getBoundingClientRect().top
    - reply.previousElementSibling!.getBoundingClientRect().bottom);
  check(gaps.length === 4 && gaps[0] > 0 && gaps.every(gap => gap === gaps[0]),
    `Command response gaps differ: ${gaps}`);
  document.querySelector<HTMLElement>('.quest-inline-started .quest-link')!.click();
  check(commands.pop() === 'quest info athens-clay-for-the-wheel', 'Acceptance name did not open quest info');
  check(workButton()?.textContent?.trim() === 'WASH CLAY', 'Info has no work button');
  workButton()!.click();
  check(commands.length === 1 && commands[0] === 'wash clay', 'Button dispatched the wrong command');
  store.state.game.room = { ...room, actions: [] };
  await frame();
  check(!workButton(), 'Busy work button remained visible');
  store.state.game.room = { ...room, key: 'room.115' };
  await frame();
  check(!workButton(), 'Work button remained in a different room');
  store.state.game.room = room;
  await frame();
  check(!!workButton(), 'Returning did not restore the work button');
  store.state.game.room = { ...room, actions: ['strain clay'] };
  await frame();
  check(!workButton(), 'Old step button remained after advancing');
  store.state.game.room = room;
  store.state.game.last_message[info.type] = {};
  await frame();
  check(!workButton(), 'Historical info retained a work button');
  store.state.game.last_message[info.type] = store.state.game.messages[7];
  await frame();
  const abandonedQuest = { ...quest, status: 'resolved', resolution: 'abandoned' };
  store.state.game.messages.push(echo('quest abandon 33'), {
    type: 'quest.instance.resolved', message_id: 9, data: { quest: abandonedQuest },
  });
  store.state.game.messages[8].message_id = 8;
  await frame();
  document.querySelector<HTMLElement>('.quest-inline-abandoned .quest-link')!.click();
  check(commands.pop() === 'quest info athens-clay-for-the-wheel', 'Abandonment name did not open quest info');
  // Render the mocked info response after verifying that the link dispatched
  // the same read-only command as acceptance. No real quest is abandoned.
  store.state.game.messages.push({ ...echo('quest info athens-clay-for-the-wheel'), message_id: 10 }, {
    type: info.type, message_id: 11, data: { subcommand: 'info', quest: abandonedQuest },
  });
  store.state.game.last_message[info.type] = store.state.game.messages[11];
  await frame();
  document.querySelector('#results')!.textContent = `PASS: ${assertions} checks; all four command-response gaps = ${gaps[0]}px`;
}
run().catch(error => {
  document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
  console.error(error);
});
