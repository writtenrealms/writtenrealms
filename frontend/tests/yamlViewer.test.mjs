import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { buildYamlView, visibleYamlRows, yamlSourceSegments } = await server.ssrLoadModule('/src/core/yamlViewer.ts');
const { describeYamlText, yamlTextExcerpt } = await server.ssrLoadModule('/src/core/yamlTextPreview.ts');

test('large text values have a bounded preview and accurate logical line counts', () => {
  const names = Array.from({ length: 594 }, (_, index) => `Reserved ${index + 1}`).join('\n') + '\n';
  const summary = describeYamlText(names);
  assert.equal(summary.lines, 594);
  assert.equal(summary.long, true);
  const excerpt = yamlTextExcerpt(names);
  assert.equal(excerpt.text, 'Reserved 1\nReserved 2\nReserved 3…');
  assert.equal(excerpt.matched, false);
  assert.equal(describeYamlText(names.replaceAll('\n', '\r\n')).lines, 594);
  assert.equal(describeYamlText(names.replaceAll('\n', '\r')).lines, 594);
  assert.equal(describeYamlText('One\nTwo\n').long, false);
  assert.equal(describeYamlText('line\n'.repeat(6)).long, false);
  assert.equal(describeYamlText('line\n'.repeat(7)).long, true);
  assert.equal(describeYamlText('a'.repeat(480)).long, false);
  assert.equal(describeYamlText('a'.repeat(481)).long, true);
});

test('search finds a late match in long text without rendering the whole value', () => {
  const names = Array.from({ length: 594 }, (_, index) => `Reserved ${index + 1}`).join('\n') + '\n';
  const excerpt = yamlTextExcerpt(names, 'reserved 594');
  assert.equal(excerpt.matched, true);
  assert.equal(excerpt.text, '…Reserved 594');
  assert.equal(yamlTextExcerpt(names, 'name_exclusions').matched, false);
  const paragraph = 'Before '.repeat(100) + '[literal.*] ' + 'after '.repeat(100);
  const match = yamlTextExcerpt(paragraph, '[literal.*]');
  assert.ok(match.text.includes('[literal.*]'));
  assert.ok(match.text.length <= 242);
  assert.equal(match.matched, true);
  assert.equal(yamlTextExcerpt(paragraph, '[not present]').matched, false);
  const unicode = yamlTextExcerpt('İ'.repeat(500) + 'MATCH at the end', 'match');
  assert.ok(unicode.text.includes('MATCH at the end'));
  assert.ok(!/[\uD800-\uDBFF]$/.test(yamlTextExcerpt('a'.repeat(239) + '😀 more text').text.replace(/…$/, '')));
});

test('preserves scalar types, large integers, empty collections, and multiline prose', () => {
  const model = buildYamlView('spec:\n  enabled: false\n  text: "false"\n  none: null\n  empty: ""\n  list: []\n  map: {}\n  big: 9007199254740993\n  ratio: 0.2\n  description: |\n    One\n    Two\n');
  assert.equal(model.error, '');
  const values = Object.fromEntries(model.nodes.map(node => [node.key, node]));
  assert.equal(values.enabled.type, 'boolean');
  assert.equal(values.enabled.value, 'No (false)');
  assert.equal(values.text.type, 'string');
  assert.equal(values.none.type, 'null');
  assert.equal(values.empty.value, '""');
  assert.equal(values.list.type, 'list');
  assert.equal(values.map.type, 'map');
  assert.equal(values.big.value, '9007199254740993');
  assert.equal(values.ratio.value, '0.2');
  assert.equal(values.description.value, 'One\nTwo\n');
  assert.equal(values.description.line, 10);
});

test('source highlighting preserves every byte, comments, CRLF, Unicode, and document boundaries', () => {
  const source = '# World\r\nkind: world\r\nspec:\r\n  name: Phálanx\r\n  description: |\r\n    <img src=x onerror=alert(1)>\r\n---\r\nkind: room\r\nmetadata: {ref: room@1}\r\n';
  const model = buildYamlView(source);
  assert.equal(model.error, '');
  assert.equal(model.documents, 2);
  assert.equal(model.nodes.filter(node => node.parent === -1).length, 2);
  assert.equal(yamlSourceSegments(source, model.tokens).map(segment => segment.text).join(''), source);
  assert.ok(model.tokens.some(token => token.type === 'key'));
});

test('invalid or ambiguous YAML returns an explanation instead of a misleading partial tree', () => {
  for (const source of ['spec: [broken', 'name: one\nname: two\n', 'field: !unknown value', '? [complex, key]\n: value']) {
    const model = buildYamlView(source);
    assert.ok(model.error, source);
    assert.deepEqual(model.nodes, []);
    assert.deepEqual(model.tokens, []);
  }
});

test('anchors stay literal references, including recursive aliases', () => {
  const model = buildYamlView('spec: &loop\n  self: *loop\n  second: *loop\n');
  assert.equal(model.error, '');
  assert.equal(model.nodes.length, 4);
  assert.equal(model.nodes[2].value, '*loop');
  assert.equal(model.nodes[2].type, 'alias');
  assert.match(model.nodes[1].note, /&loop/);
});

test('collapsed branches are skipped, and search reveals matching values with their ancestors', () => {
  const model = buildYamlView('spec:\n  death_routing:\n    routes:\n      - destination: room@10\n  unrelated: other\n');
  const collapsed = new Set();
  assert.equal(visibleYamlRows(model.nodes, collapsed, '').length, 1);
  const found = visibleYamlRows(model.nodes, collapsed, 'room@10');
  assert.deepEqual(found.map(node => node.key), ['Document 1', 'spec', 'death_routing', 'routes', '[0]', 'destination']);
  assert.ok(!found.some(node => node.key === 'unrelated'));
  assert.ok(visibleYamlRows(model.nodes, collapsed, 'death routing').some(node => node.key === 'destination'));
  assert.equal(visibleYamlRows(model.nodes, collapsed, 'no match').length, 0);
  assert.equal(collapsed.size, 0, 'Search must preserve user expansion state');
});

test('treats prototype-like keys and HTML as ordinary authored text', () => {
  const model = buildYamlView('__proto__: safe\nconstructor: text\nhtml: "<script>alert(1)</script>"\n');
  assert.equal(model.error, '');
  assert.equal(model.nodes[1].key, '__proto__');
  assert.equal(model.nodes[3].value, '<script>alert(1)</script>');
});

test('comments attached to documents, keys, and values remain searchable', () => {
  const source = '# opening note\n\nkind: world\nspec:\n  # field note\n  name: Phalanx # inline note\n# closing note\n';
  const model = buildYamlView(source);
  assert.equal(model.error, '');
  for (const query of ['opening note', 'field note', 'inline note', 'closing note']) {
    assert.ok(visibleYamlRows(model.nodes, new Set(), query).length, query);
  }
});

test('bounds large and deeply nested documents without losing the source fallback', () => {
  assert.match(buildYamlView('a'.repeat(2_000_001)).error, /too large/);
  const nested = Array.from({ length: 82 }, (_, index) => `${' '.repeat(index * 2)}a:\n`).join('');
  assert.match(buildYamlView(nested).error, /too complex/);
});

test('large exports keep collapsed row traversal bounded to visible documents', () => {
  const source = Array.from({ length: 1000 }, (_, index) => `kind: room\nmetadata: {ref: room@${index}}\nspec:\n  name: Room ${index}\n  description: A quiet room.\n`).join('---\n');
  const start = performance.now();
  const model = buildYamlView(source);
  const parseMs = performance.now() - start;
  assert.equal(model.error, '');
  assert.equal(model.documents, 1000);
  assert.equal(model.nodes.length, 7000);
  assert.equal(visibleYamlRows(model.nodes, new Set(), '').length, 1000);
  assert.ok(visibleYamlRows(model.nodes, new Set(), 'room@999').some(node => node.value === 'room@999'));
  console.log(`YAML viewer: 1,000 documents / 7,000 nodes parsed in ${parseMs.toFixed(0)}ms; UI parsing runs in a worker.`);
});
