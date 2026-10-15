// Real LIST, hover directive, item lookup, and mobile modal. Commands stay local.
import { createApp, h, nextTick } from 'vue';
import store from '../../src/store';
import { interactive } from '../../src/core/directives';
import List from '../../src/components/game/console/List.vue';
import Lookup from '../../src/components/game/lookup/Lookup.vue';
import Modal from '../../src/components/ui/Modal.vue';
import { merchantLookupState } from '../fixtures/merchantItemLookup.mjs';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
const commands: string[] = [];
store.hotUpdate({ modules: { game: {
  namespaced: true,
  actions: { cmd: (_context, command: string) => { commands.push(command); } },
} } });
Object.assign(store.state.game, merchantLookupState(innerWidth <= 768 || params.has('mobile')));
createApp({ render: () => h('div', [
  h('h2', "Armorer's Workshop"),
  h(List, { message: store.state.game.last_message['cmd.list.success'] }),
  store.state.game.lookup && !store.state.game.is_mobile ? h('div', {
    style: { marginTop: '24px' },
    onMouseover: () => store.commit('game/popup_hover_set', true),
    onMouseleave: () => {
      store.commit('game/popup_hover_set', false);
      store.commit('game/lookup_clear');
    },
  }, [h(Lookup)]) : null,
  store.state.ui.modal.isOpen ? h(Modal) : null,
]) }).use(store).directive('interactive', interactive).mount('#app');

const frame = async () => { await nextTick(); await new Promise(requestAnimationFrame); };
const hover = (element: HTMLElement) => element.dispatchEvent(new MouseEvent('mouseenter'));
let checks = 0;
const check = (condition: boolean, message: string) => {
  if (!condition) throw new Error(message);
  checks += 1;
};
async function run() {
  await document.fonts.ready;
  await frame();
  const item = document.querySelector<HTMLElement>('.merchant-list .interactive')!;
  hover(item);
  await new Promise(resolve => setTimeout(resolve, 200));
  await frame();
  if (store.state.game.is_mobile) {
    check(!store.state.game.lookup, 'Mobile hover opened the lookup');
    item.click();
    await frame();
    check(!!document.querySelector('#modal .lookup-item'), 'Tap did not open the item modal');
  } else {
    check(!!document.querySelector('.lookup-item'), 'Hover did not open the item lookup');
  }
  const lookup = document.querySelector<HTMLElement>('.lookup-item')!;
  check(lookup.textContent!.includes('worn on head'), 'Equipment slot is missing');
  check(lookup.textContent!.includes('Constitution') && lookup.textContent!.includes('Strength'), 'Attributes are missing');
  check(lookup.textContent!.includes('168') && lookup.textContent!.includes('(+68)'), 'Armor comparison is missing');
  check(!lookup.textContent!.includes('Health:'), 'Item rendered character health');
  check(commands.length === 0, 'Opening the item sent a command');
  const buy = lookup.querySelector<HTMLElement>('.action.primary')!;
  check(buy.textContent === 'BUY FOR 75 OBOLS', 'Purchase price is missing');
  buy.click();
  await frame();
  check(commands.length === 1 && commands[0] === 'buy merchant_stock_entry.7 from room.158', 'Buy sent the wrong command');
  check(!store.state.game.lookup && !store.state.ui.modal.isOpen, 'Buy left the lookup open');
  document.querySelector('#results')!.textContent = `PASS: ${checks} checks (${store.state.game.is_mobile ? 'mobile' : 'desktop'}, ${innerWidth}px)`;
  if (params.has('preview')) {
    if (store.state.game.is_mobile) item.click();
    else hover(item);
  }
}
run().catch(error => {
  document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
  console.error(error);
});
