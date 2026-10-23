// Real character page with disposable in-memory API responses.
import { createApp, h, nextTick } from 'vue';
import { createRouter, createMemoryHistory, RouterView } from 'vue-router';
import { createStore } from 'vuex';
import axios from 'axios';
import CharacterPage from '../../src/views/lobby/CharacterPage.vue';
import Notification from '../../src/components/ui/Notification.vue';
import ui from '../../src/store/modules/ui';
import { characterItemsPage } from '../fixtures/characterItems.mjs';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
const data = characterItemsPage();
if (params.has('online')) { data.in_game = true; data.can_manage_items = false; }
if (params.has('public')) { data.view = 'public'; Object.assign(data, { equipment: [], created_ts: '2026-10-23', level: 20 }); }
let writes = 0;
axios.defaults.adapter = async config => {
  if (config.method === 'post') {
    writes++;
    const body = JSON.parse(config.data);
    if (params.has('failure')) throw { response: { data: { detail: 'Fen is in the game. Manage items there.' } } };
    if (body.action !== 'salvage') throw new Error('Only salvage is implemented in this fixture');
    const index = data.character.inventory.findIndex(item => item.key === body.item);
    if (index < 0) throw new Error('Unknown item');
    data.character.inventory.splice(index, 1);
    data.character.materials.bronze += 3;
  }
  return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
};
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/characters/:player_id', component: CharacterPage },
  { path: '/worlds/:world_id', name: 'lobby_world_details', component: {} },
] });
const store = createStore({ state: { auth: { user: { is_temporary: false } } }, modules: { ui } });
await router.push('/characters/4');
createApp({ render: () => h('div', [h(RouterView), store.state.ui.notification ? h(Notification) : null]) }).use(store).use(router).mount('#app');
const frame = async () => { await nextTick(); await new Promise(requestAnimationFrame); await nextTick(); };
let checks = 0;
function check(condition: boolean, message: string) { if (!condition) throw new Error(message); checks++; }
async function run() {
  await frame(); await frame();
  check(document.documentElement.scrollWidth <= innerWidth, 'Horizontal page overflow');
  const salvage = [...document.querySelectorAll<HTMLButtonElement>('button')].filter(el => el.textContent === 'SALVAGE');
  if (params.has('public')) {
    check(salvage.length === 0 && !document.querySelector('.item-actions'), 'Public view exposes actions');
  } else {
    check(salvage.length === 3, 'Only salvageable carried gear, spoils, and empty bags get actions');
    check([...document.querySelectorAll('.item-actions button')].every(el => el.classList.contains('btn-thin')), 'Inconsistent item action styles');
    const bag = document.querySelector<HTMLButtonElement>('[aria-label="Show what\'s in a travel satchel"]')!;
    bag.click(); await frame();
    check(!document.querySelector('.bag-content-row button[aria-label^="Salvage"]'), 'Bag contents must be taken out before salvage');
    if (params.has('online')) {
      check(salvage.every(el => el.disabled), 'Online character can salvage');
      check(writes === 0, 'Online page wrote items');
    } else if (params.has('test')) {
      salvage[0].click(); salvage[0].click(); await frame(); await frame();
      check(writes === 1, 'Double click submitted twice');
      if (params.has('failure')) {
        check(!!document.querySelector('[role="alert"]'), 'Missing error notification');
        check(data.character.inventory.length === 6 && data.character.materials.bronze === 1, 'Failure lost an item or credited materials');
      } else {
        check(!document.querySelector('[aria-label="Salvage a Persian war spear"]'), 'Salvaged item still shown');
        check(data.character.materials.bronze === 4, 'Material balance was not updated');
        check(document.querySelector('[role="status"]')?.textContent?.includes('Salvaged a Persian war spear.'), 'Missing success feedback');
      }
    }
  }
  document.querySelector('#results')!.textContent = `${checks} checks passed`;
}
run().catch(error => { document.querySelector('#results')!.textContent = `FAILED: ${error.message}`; throw error; });
