import { createApp, h, nextTick, ref } from 'vue';
import { createStore } from 'vuex';
import { setLucideProps } from '@lucide/vue';
import ManifestYamlEditor from '../../src/components/builder/world/ManifestYamlEditor.vue';
import '../../src/styles/app.scss';

const exclusions = ['Achaemenes', 'Achaimenes', 'Achelous',
  ...Array.from({ length: 591 }, (_, index) => `Reserved ${index + 4}`)].join('\n') + '\n';
const sample = `# Configuration for the authored world
apiVersion: writtenrealms.com/v1alpha3
kind: world
spec:
  name: Phalanx
  name_exclusions: |
${exclusions.trimEnd().split('\n').map(name => `    ${name}`).join('\n')}
  is_public: true
  starting_room: room@87
  pvp_mode: disabled
  death_currency_penalty_pct: 0.2
  description: |
    Dawn breaks over the Aegean. Sailors gather beside the harbor,
    trading stories of distant islands and the armies beyond the sea.
  death_routing:
    routes:
      - when:
          eq: [zone.id, zone@1]
        destination: room@10
  reserved_names: []
  notes: "<img src=x onerror=alert(1)>"
`;
const params = new URLSearchParams(location.search);
const source = ref(params.has('large') ? `kind: world\nspec:\n${Array.from({ length: 10_000 }, (_, index) => `  field_${index}: value ${index}\n`).join('')}` : sample);
const saved = source.value;
const store = createStore({ mutations: { 'ui/notification_set': () => {}, 'ui/notification_set_error': () => {} } });
let saves = 0;
setLucideProps({ size: 18, strokeWidth: 1.75 });
createApp({ render: () => h('main', { style: 'max-width:1000px;margin:40px auto;padding:0 16px' }, [
  h('h1', 'World YAML'),
  h('p', 'Edit the current world configuration manifest.'),
  h(ManifestYamlEditor, { modelValue: source.value, loadedValue: saved, 'onUpdate:modelValue': value => { source.value = value; }, onSave: () => { saves++; } }),
]) }).use(store).mount('#app');

const button = (label: string) => Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(el => el.textContent?.trim() === label)!;
const wait = async (predicate: () => boolean) => {
  const start = performance.now();
  while (!predicate()) {
    if (performance.now() - start > 12_000) throw new Error('Timed out waiting for viewer');
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  await nextTick();
};
let checks = 0;
const check = (condition: boolean, message: string) => { if (!condition) throw new Error(message); checks++; };
async function run() {
  let copied = '';
  let rejectCopy = false;
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
    writeText: async (text: string) => { if (rejectCopy) throw new Error('Clipboard unavailable'); copied = text; },
  } });
  button('VIEW YAML').focus(); button('VIEW YAML').click();
  await wait(() => !!document.querySelector('.yaml-viewer-row'));
  const dialog = document.querySelector<HTMLDialogElement>('dialog')!;
  check(dialog.matches(':modal'), 'Viewer is not a native modal');
  check(dialog.contains(document.activeElement), 'Focus did not enter dialog');
  check(dialog.scrollWidth <= dialog.clientWidth, 'Dialog overflows horizontally');
  const bounds = dialog.getBoundingClientRect();
  check(bounds.left >= 8 && bounds.top >= 8 && bounds.right <= innerWidth - 8 && bounds.bottom <= innerHeight - 8,
    'Viewer must leave the origin page visible around every edge');
  check(getComputedStyle(dialog, '::backdrop').backgroundColor.startsWith('rgba('), 'Backdrop must be translucent');
  check(document.querySelectorAll('.yaml-viewer-row').length <= 300, 'Initial rendering is unbounded');
  if (params.has('large')) {
    button('SHOW NEXT 300 FIELDS').click(); await nextTick();
    check(document.querySelectorAll('.yaml-viewer-row').length === 600, 'Progressive field rendering failed');
  }
  check(!dialog.querySelector('img'), 'Authored HTML became markup');
  if (!params.has('large')) {
    const textValue = dialog.querySelector<HTMLElement>('.yaml-text-value')!;
    const toggleText = textValue.querySelector<HTMLButtonElement>('button')!;
    check(!textValue.querySelector('.yaml-text-full'), 'Long text was mounted before opening');
    check(textValue.textContent!.includes('594 lines'), 'Long text has no accurate line count');
    check(!textValue.textContent!.includes('Reserved 594'), 'Collapsed text renders the complete value');
    const compactHeight = textValue.getBoundingClientRect().height;
    check(compactHeight < 170, 'Long text preview takes too much vertical space');
    toggleText.click(); await nextTick();
    const fullText = textValue.querySelector<HTMLElement>('.yaml-text-full')!;
    check(fullText.textContent === exclusions, 'Expanded text lost content');
    check(fullText.clientHeight <= 256 && fullText.scrollHeight > fullText.clientHeight, 'Full value is not in a bounded scroll area');
    check(fullText.tabIndex === 0, 'Full text cannot be scrolled with a keyboard');
    toggleText.click(); await nextTick();
    check(textValue.getBoundingClientRect().height === compactHeight, 'Collapse did not restore compact height');
    const textSearch = document.querySelector<HTMLInputElement>('#yaml-viewer-search')!;
    textSearch.value = 'reserved 594'; textSearch.dispatchEvent(new Event('input', { bubbles: true }));
    await wait(() => !!dialog.querySelector('.yaml-text-preview')?.textContent?.includes('Reserved 594'));
    check(!dialog.querySelector('.yaml-text-full'), 'Search expanded the entire long value');
    check(dialog.textContent!.includes('Matching excerpt'), 'Search excerpt is not identified');
    textSearch.value = ''; textSearch.dispatchEvent(new Event('input', { bubbles: true }));
    await wait(() => !button('EXPAND ALL').disabled);
  }
  button('COLLAPSE ALL').click(); await nextTick();
  check(document.querySelectorAll('.yaml-viewer-row').length === 1, 'Collapse all left descendants visible');
  button('EXPAND ALL').click(); await nextTick();
  check(document.querySelectorAll('.yaml-viewer-row').length > 10, 'Expand all did not show fields');
  check(!dialog.querySelector('.yaml-text-full'), 'Expand all should not expand long text');
  const search = document.querySelector<HTMLInputElement>('#yaml-viewer-search')!;
  search.value = params.has('large') ? 'value 9999' : 'room@10';
  search.dispatchEvent(new Event('input', { bubbles: true }));
  await wait(() => button('EXPAND ALL').disabled);
  check(dialog.textContent!.includes(params.has('large') ? 'value 9999' : 'room@10'), 'Search lost nested value');
  button('YAML SOURCE').click(); await nextTick();
  const raw = dialog.querySelector('pre code')?.textContent ?? dialog.querySelector('textarea')?.value;
  check(raw === source.value, 'Source view changed original text');
  const copyButton = Array.from(dialog.querySelectorAll('button')).find(el => el.textContent?.trim() === 'COPY YAML')!;
  copyButton.click(); await nextTick();
  check(copied === source.value, 'Copy did not preserve the complete original YAML');
  rejectCopy = true; copyButton.click();
  await wait(() => dialog.textContent!.includes('Copy failed.'));
  check(!!dialog.querySelector('[role="status"]')?.textContent?.includes('Copy failed.'), 'Clipboard failure was not announced');
  dialog.dispatchEvent(new PointerEvent('pointerdown', { clientX: bounds.left + 20, clientY: bounds.top + 20 }));
  dialog.dispatchEvent(new MouseEvent('click', { clientX: 1, clientY: 1 })); await nextTick();
  check(dialog.isConnected, 'Dragging a text selection outside the modal dismissed it');
  dialog.querySelector<HTMLButtonElement>('[aria-label="Close YAML viewer"]')!.click(); await nextTick();
  check(!document.querySelector('dialog'), 'Close left modal mounted');
  check(document.activeElement === button('VIEW YAML'), 'Focus did not return to opener');
  check(source.value === saved && saves === 0, 'Viewing saved or changed editor contents');
  button('VIEW YAML').click();
  await wait(() => !!document.querySelector('dialog[open]'));
  const reopened = document.querySelector('dialog')!;
  reopened.dispatchEvent(new PointerEvent('pointerdown', { clientX: 1, clientY: 1 }));
  reopened.dispatchEvent(new MouseEvent('click', { clientX: 1, clientY: 1 })); await nextTick();
  check(!document.querySelector('dialog'), 'Backdrop click did not close the viewer');
  check(document.activeElement === button('VIEW YAML'), 'Backdrop dismissal did not restore focus');
  source.value = 'spec: [broken'; await nextTick(); button('VIEW YAML').click();
  await wait(() => !!document.querySelector('.yaml-viewer-error'));
  check(document.querySelector<HTMLTextAreaElement>('dialog textarea')!.value === source.value, 'Invalid YAML source was lost');
  document.querySelector<HTMLButtonElement>('[aria-label="Close YAML viewer"]')!.click(); await nextTick();
  source.value = saved.replace('Phalanx', 'Phalanx draft'); await nextTick(); button('VIEW YAML').click();
  await wait(() => !!document.querySelector('.yaml-viewer-row'));
  if (!params.has('large')) check(!!document.querySelector('.yaml-viewer-draft'), 'Unsaved draft status missing');
  document.querySelector('#results')!.textContent = `PASS: ${checks} YAML viewer checks at ${innerWidth} × ${innerHeight}`;
}
if (params.has('test')) void run().catch(error => { document.querySelector('#results')!.textContent = `FAIL: ${error.message}`; console.error(error); });
