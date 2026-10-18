// Real page and editor with an in-memory adapter; no backend changes.
import { createApp, h, nextTick } from 'vue';
import { createRouter, createMemoryHistory, RouterView } from 'vue-router';
import { createStore } from 'vuex';
import axios from 'axios';
import WorldInformation from '../../src/views/lobby/WorldInformation.vue';
import '../../src/styles/app.scss';

const params = new URLSearchParams(location.search);
const codeSample = 'Dawn of the fifth century BC. The Achaemenid Empire has swallowed the East. Thrace and Macedonia have already fallen. Now the armies turn west, threatening the freedom of the Greek cities and the existence of Olympus.\n    Keep this indentation.\n' + 'long-command-'.repeat(20) + '\n';
const sample = '```text\n' + codeSample + '```\n\n## A world on the edge of war\n\nThe Persian Empire is moving west. Explore **ancient Greece**, meet its people, and choose your place in the story.\n\n## Getting started\n\n- Explore Athens and speak with the people in the agora.\n- Type `look` to examine your surroundings.\n- Read the [player guides](/docs/players/) for the basics.\n\n> Take your time. There is more to this world than the next battle.\n\n## At a glance\n\n| Setting | Play style |\n| --- | --- |\n| Ancient Greece | Exploration and adventure |';
let content = params.has('empty') ? '' : sample;
let editable = !params.has('reader');
let failure = false;
let writes = 0;
axios.defaults.adapter = async config => {
  if (!config.url?.endsWith('/information/')) throw new Error('Unexpected request');
  if (config.method === 'patch') {
    writes++;
    if (failure) throw { response: { status: 500 } };
    content = JSON.parse(config.data).information;
    return { config, status: 200, statusText: 'OK', headers: {}, data: { information: content } };
  }
  return { config, status: 200, statusText: 'OK', headers: {}, data: {
    id: Number(config.url.split('/')[3]), name: 'Phalanx', information: content, can_edit: editable,
  } };
};
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/worlds/:world_id/about', name: 'lobby_world_information', component: WorldInformation },
  { path: '/worlds/:world_id', name: 'lobby_world_details', component: { template: '<p>World lobby</p>' } },
  { path: '/login', name: 'login', component: {} },
] });
const store = createStore({ getters: { isAuthenticated: () => true } });
await router.push('/worlds/12/about');
createApp({ render: () => h(RouterView) }).use(store).use(router).mount('#app');

const frame = async () => { await nextTick(); await new Promise(requestAnimationFrame); await nextTick(); };
const button = (text: string) => Array.from(document.querySelectorAll('button')).find(node => node.textContent?.trim() === text)!;
let checks = 0;
function check(condition: boolean, message: string) { if (!condition) throw new Error(message); checks++; }
function checkCodeBlock() {
  const pre = document.querySelector<HTMLPreElement>('.markdown-content pre')!;
  const code = pre?.querySelector('code');
  check(!!pre && !!code, 'Code block missing');
  check(pre.scrollWidth <= pre.clientWidth, 'Code block requires horizontal scrolling');
  check(code!.getBoundingClientRect().height > parseFloat(getComputedStyle(pre).lineHeight) * 3, 'Long code lines did not wrap');
  check(getComputedStyle(code!).borderWidth === '0px', 'Block code inherited the inline-code border');
  check(code!.textContent === codeSample, 'Code whitespace or indentation changed');
}
async function run() {
  await frame(); await frame();
  check(document.documentElement.scrollWidth <= innerWidth, 'Page overflows horizontally');
  check(document.querySelectorAll('.information-heading h1').length === 1 && !document.querySelector('.information-label'), 'Redundant page heading');
  if (!params.has('empty')) checkCodeBlock();
  if (params.has('reader')) {
    check(!button('EDIT') && !document.querySelector('textarea'), 'Reader has editing controls');
    check(!!document.querySelector('.markdown-content h2'), 'Reader cannot see rendered information');
  } else {
    if (!params.has('empty')) button('EDIT').click();
    await frame();
    const editor = document.querySelector<HTMLTextAreaElement>('textarea')!;
    check(!!editor, 'Builder has no editor');
    check(document.activeElement === editor, 'Editor did not receive keyboard focus');
    const draft = '## Draft\n\nKeep **my changes**.  \nNext line.\n';
    editor.value = draft;
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    await frame();
    const originalConfirm = window.confirm;
    let prompted = false;
    window.confirm = () => { prompted = true; return false; };
    try {
      await router.push('/worlds/99');
      check(prompted && router.currentRoute.value.path === '/worlds/12/about', 'Unsaved navigation was not blocked');
      check(editor.value === draft, 'Cancelled navigation lost the draft');
    } finally {
      window.confirm = originalConfirm;
    }
    button('PREVIEW').click(); await frame();
    check(!!document.querySelector('.information-preview strong'), 'Preview is not rendered Markdown');
    button('WRITE').click(); await frame();
    check(editor.value === draft, 'Preview discarded the draft');
    editor.value = '```text\n' + codeSample + '```';
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    button('PREVIEW').click(); await frame();
    checkCodeBlock();
    button('WRITE').click(); await frame();
    editor.value = draft;
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    await frame();
    failure = true;
    button('SAVE').click(); await frame(); await frame();
    check(!!document.querySelector('[role="alert"]'), 'Save error missing');
    check(editor.value === draft && !editor.disabled, 'Failed save lost or locked the draft');
    failure = false;
    button('SAVE').click(); await frame(); await frame();
    check(content === draft && !!document.querySelector('[role="status"]'), 'Save did not preserve Markdown');
    check(!document.querySelector('textarea'), 'Saved page did not return to reading');
    button('EDIT').click(); await frame();
    const again = document.querySelector<HTMLTextAreaElement>('textarea')!;
    again.value = 'Discard this'; again.dispatchEvent(new Event('input', { bubbles: true }));
    button('CANCEL').click(); await frame();
    check(content === draft && !!document.querySelector('.markdown-content strong'), 'Cancel changed saved text');
    button('EDIT').click(); await frame();
    const clear = document.querySelector<HTMLTextAreaElement>('textarea')!;
    clear.value = ''; clear.dispatchEvent(new Event('input', { bubbles: true }));
    button('SAVE').click(); await frame(); await frame();
    check(content === '' && document.body.textContent!.includes("haven't added"), 'Clearing content failed');
    editable = false;
    await router.push('/worlds/13/about'); await frame(); await frame();
    check(!document.querySelector('textarea') && !button('EDIT'), 'Navigation retained builder controls');
    check(writes === 3, 'Unexpected saves');
  }
  document.querySelector('#results')!.textContent = `PASS: ${checks} world information checks at ${innerWidth} × ${innerHeight}`;
}
if (params.has('test')) void run().catch(error => {
  document.querySelector('#results')!.textContent = `FAIL: ${error.message}`;
  console.error(error);
});
