// Open /tests/browser/questLogLayout.html on the Vite dev server. Runs against
// the real modal, quest cards, and global CSS; repeat at desktop/mobile sizes.
// All quest data is local to this page. No backend requests or writes are made.
import { createApp, markRaw, nextTick } from 'vue';
import { createStore } from 'vuex';
import axios from 'axios';
import Modal from '../../src/components/ui/Modal.vue';
import QuestLog from '../../src/components/game/QuestLog.vue';
import '../../src/styles/app.scss';

const quest = (id: number) => ({
  id, status: 'resolved', resolution: 'complete',
  template: { slug: `garden-supplies-${id}`, name: 'Supplies for the Garden', quest_type: 'quest' },
  current_step: {
    text: { body: '"The garden needs a little care before the next rain," the gardener says. "There is space for another row along the sunny wall."\n\nShe lays a packet of seeds on the table and points toward a shelf of empty pots, each one scrubbed and ready for planting.\n\n"Take these to the prepared bed, cover them lightly, and give the soil a gentle watering. We will see what grows."' },
    recap: 'The gardener has given you seeds for the new planting bed.',
    objectives: [],
  },
  repeatability: { mode: 'always', state: 'ready' },
});

const showActive = new URLSearchParams(location.search).has('active');
const activeQuest = {
  ...quest(7), status: 'active', resolution: null,
  template: { slug: 'workshop-supplies', name: 'Supplies for the Workshop', quest_type: 'quest' },
  current_step: {
    text: { body: '"Straight boards on the left, short pieces on the right," the carpenter says. "Sort this bundle, then carry the usable timber to the bench."\n\nShe clears a space beside the stack and sets down a pair of gloves.' },
    recap: 'SORT TIMBER beside the workbench.', objectives: [],
    room_action: { command: 'sort timber', room_key: 'room.10', world_id: 1 },
  },
};
const commands: string[] = [];
let closed = false;

let releaseResponse!: () => void;
const responseReady = new Promise<void>(resolve => { releaseResponse = resolve; });
axios.defaults.adapter = async config => {
  await responseReady;
  return { config, status: 200, statusText: 'OK', headers: {}, data: {
    active: showActive ? [activeQuest] : [], repeatable: [quest(8)],
    resolved: Array.from({ length: 20 }, (_, index) => quest(100 + index)),
  } };
};
const store = createStore({ state: {
  game: { player: { id: 1, is_builder: true }, instance_time_control: null,
    world: { id: 1 }, room: { key: 'room.10', actions: ['sort timber'] } },
  ui: { modal: { component: markRaw(QuestLog), props: {}, options: {} } },
}, actions: {
  'game/cmd': (_context, command: string) => { commands.push(command); },
}, mutations: {
  'ui/modal/close': () => { closed = true; },
} });
createApp(Modal).use(store).mount('#app');

const frame = async () => {
  await nextTick();
  await new Promise(requestAnimationFrame);
};
const element = (selector: string) => document.querySelector<HTMLElement>(selector)!;
let assertions = 0;
function check(condition: boolean, message: string) {
  if (!condition) throw new Error(message);
  assertions += 1;
}

async function run() {
  await frame();
  const modal = element('#quest_log');
  const initial = modal.getBoundingClientRect();
  releaseResponse();
  await frame();
  await frame();
  const tabs = element('[role=tablist]');
  const tabTop = tabs.getBoundingClientRect().top;
  const checkLayout = () => {
    const bounds = modal.getBoundingClientRect();
    check(bounds.height === initial.height && bounds.top === initial.top, 'Modal moved or resized');
    check(tabs.getBoundingClientRect().top === tabTop, 'Tabs moved vertically');
    check(tabs.scrollHeight <= tabs.clientHeight, 'Tab row has vertical overflow');
    check(modal.scrollHeight <= modal.clientHeight, 'Outer modal scrolls instead of the quest panel');
    check(bounds.top >= 0 && bounds.bottom <= innerHeight && bounds.right <= innerWidth, 'Modal leaves viewport');
  };
  if (!showActive) check(element('[role=tabpanel]').textContent!.includes('No active quests.'), 'Empty tab missing');
  checkLayout();
  for (const tab of ['repeatable', 'resolved', 'active', 'repeatable']) {
    element(`#quest-log-tab-${tab}`).click();
    await frame();
    checkLayout();
    if (tab !== 'active') {
      check(!element('[role=tabpanel]').textContent!.includes('ABANDON'), 'Inactive quest has an abandon action');
    }
    check(element('[role=tabpanel]').scrollTop === 0, 'New tab did not start at top');
    if (tab === 'resolved') {
      const panel = element('[role=tabpanel]');
      check(panel.scrollHeight > panel.clientHeight, 'Long quest list is not scrollable');
      panel.scrollTop = panel.scrollHeight;
      await frame();
      check(panel.scrollTop > 0, 'Quest list cannot scroll');
      checkLayout();
    }
  }
  const disclosure = element('.quest-card [aria-expanded]');
  check(!!disclosure, 'Quest disclosure missing');
  disclosure.click();
  await frame();
  checkLayout();
  if (showActive) {
    element('#quest-log-tab-active').click();
    await frame();
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('.quest-actions button'));
    check(buttons.map(button => button.textContent!.trim()).join(',') === 'SORT TIMBER,INFO,ABANDON',
      'Abandon must be immediately after Info');
    buttons[2].click();
    await frame();
    check(commands.length === 1 && commands[0] === 'quest abandon 7', 'Abandon targeted the wrong quest attempt');
    check(closed, 'Abandon did not close the Quest Log');
  }
  disclosure.click();
  await frame();
  checkLayout();
  element('#results').textContent = `PASS: ${assertions} layout checks at ${innerWidth} × ${innerHeight}`;
}
run().catch(error => {
  element('#results').textContent = `FAIL: ${error.message}`;
  element('#results').style.color = '#ff6666';
  console.error(error);
});
