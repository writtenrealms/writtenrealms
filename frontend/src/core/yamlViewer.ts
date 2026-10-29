import { isAlias, isMap, isScalar, isSeq, LineCounter, parseAllDocuments } from 'yaml';

export interface YamlViewNode {
  id: number;
  parent: number;
  depth: number;
  end: number;
  key: string;
  label: string;
  value: string;
  type: string;
  count: number;
  line: number;
  note: string;
  search: string;
}

export interface YamlViewToken { start: number; end: number; type: string }
export interface YamlViewModel {
  nodes: YamlViewNode[];
  tokens: YamlViewToken[];
  documents: number;
  error: string;
}

export const yamlLabel = (key: string) => key
  .replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ')
  .replace(/^\w/, letter => letter.toUpperCase());

// Work with the syntax tree, never toJS(): aliases stay references, so recursive
// anchors cannot expand into cycles or exponential amounts of data.
export function buildYamlView(source: string): YamlViewModel {
  const model: YamlViewModel = { nodes: [], tokens: [], documents: 0, error: '' };
  try {
    if (source.length > 2_000_000) throw new Error('This document is too large for builder view. The full source is available below.');
    const lineCounter = new LineCounter();
    const documents = parseAllDocuments(source, { lineCounter, intAsBigInt: true });
    model.documents = documents.length;
    const errors = documents.flatMap(doc => [...doc.errors, ...doc.warnings]);
    if (errors.length) throw new Error(errors[0].message);

    const token = (node: any, type: string) => {
      if (node?.range) model.tokens.push({ start: node.range[0], end: node.range[1], type });
    };
    const addNotes = (node: YamlViewNode, notes: (string | null | undefined)[]) => {
      node.note = [...new Set([node.note, ...notes].filter(Boolean))].join('\n');
      node.search = `${node.key} ${node.label} ${node.value} ${node.note}`.toLocaleLowerCase();
    };
    const addNode = (node: any, key: string, parent: number, depth: number, label = yamlLabel(key), sourceLine?: number): YamlViewNode => {
      if (model.nodes.length >= 50_000 || depth > 80) {
        throw new Error('This document is too complex for builder view. The full source is available below.');
      }
      const type = isMap(node) ? 'map' : isSeq(node) ? 'list' : isAlias(node) ? 'alias'
        : node?.value == null ? 'null' : typeof node.value === 'bigint' ? 'number' : typeof node.value;
      const value = type === 'map' || type === 'list' ? '' : isAlias(node) ? `*${node.source}`
        : type === 'null' ? 'Not set (null)' : type === 'boolean' ? `${node.value ? 'Yes' : 'No'} (${node.value})`
          : node.value === '' ? '""' : String(node.value);
      const current: YamlViewNode = {
        id: model.nodes.length, parent, depth, end: 0, key, label, type, value,
        count: node?.items?.length || 0,
        line: sourceLine ?? (node?.range ? lineCounter.linePos(node.range[0]).line : 1),
        note: [node?.anchor && `Anchor: &${node.anchor}`, node?.tag, node?.commentBefore, node?.comment].filter(Boolean).join('\n'),
        search: '',
      };
      current.search = `${key} ${label} ${value} ${current.note}`.toLocaleLowerCase();
      model.nodes.push(current);
      if (isMap(node)) {
        for (const pair of node.items) {
          if (!isScalar(pair.key)) throw new Error('Complex mapping keys are available in YAML source view.');
          token(pair.key, 'key');
          const child = addNode(pair.value, String(pair.key.value), current.id, depth + 1, undefined,
            pair.key.range ? lineCounter.linePos(pair.key.range[0]).line : undefined);
          addNotes(child, [pair.key.commentBefore, pair.key.comment]);
        }
      } else if (isSeq(node)) {
        node.items.forEach((item, index) => addNode(item, `[${index}]`, current.id, depth + 1, `Item ${index + 1}`));
      } else token(node, type);
      current.end = model.nodes.length;
      return current;
    };
    const scalar = (node: any) => isScalar(node) ? String(node.value ?? '') : '';
    documents.forEach((doc, index) => {
      const root = doc.contents;
      const kind = isMap(root) ? scalar(root.get('kind', true)) : '';
      const name = isMap(root) ? ['spec', 'metadata'].flatMap(section => ['name', 'ref', 'slug', 'code']
        .map(key => scalar(root.getIn([section, key], true)))).find(Boolean) : '';
      const label = [documents.length > 1 ? `Document ${index + 1}` : '', yamlLabel(kind), name].filter(Boolean).join(' · ') || 'YAML document';
      const node = addNode(root, `Document ${index + 1}`, -1, 0, label);
      addNotes(node, [doc.commentBefore, doc.comment]);
    });
    model.tokens.sort((a, b) => a.start - b.start);
  } catch (error) {
    model.nodes = [];
    model.tokens = [];
    model.error = error instanceof Error ? error.message : 'Unable to read this YAML. The full source is available below.';
  }
  return model;
}

// Flat, preorder nodes let collapsed branches be skipped without recursive Vue
// components. Rendering is paged independently from the complete searchable tree.
export function visibleYamlRows(nodes: YamlViewNode[], expanded: Set<number>, query: string): YamlViewNode[] {
  const search = query.trim().toLocaleLowerCase();
  if (search) {
    const visible = new Set<number>();
    let matchingBranchEnd = -1;
    for (const node of nodes) {
      if (node.search.includes(search)) matchingBranchEnd = Math.max(matchingBranchEnd, node.end);
      if (node.id < matchingBranchEnd) visible.add(node.id);
    }
    for (let index = nodes.length - 1; index >= 0; index--) {
      if (visible.has(index) && nodes[index].parent >= 0) visible.add(nodes[index].parent);
    }
    return nodes.filter(node => visible.has(node.id));
  }
  const rows: YamlViewNode[] = [];
  for (let index = 0; index < nodes.length;) {
    const node = nodes[index];
    rows.push(node);
    index = node.count && !expanded.has(node.id) ? node.end : index + 1;
  }
  return rows;
}

export function yamlSourceSegments(source: string, tokens: YamlViewToken[]) {
  const segments: { text: string; type: string }[] = [];
  let offset = 0;
  for (const token of tokens) {
    if (token.start < offset) continue;
    if (token.start > offset) segments.push({ text: source.slice(offset, token.start), type: 'plain' });
    segments.push({ text: source.slice(token.start, token.end), type: token.type });
    offset = token.end;
  }
  segments.push({ text: source.slice(offset), type: 'plain' });
  return segments;
}
