// Only the short excerpt is mounted until the builder explicitly opens the value.
// Count lines once per value without allocating an array for a large text block.
export function describeYamlText(value: string) {
  let lines = value.length ? 1 : 0;
  for (let index = 0; index < value.length; index++) {
    if (value[index] === '\r') {
      lines++;
      if (value[index + 1] === '\n') index++;
    } else if (value[index] === '\n') lines++;
  }
  if (/[\r\n]$/.test(value)) lines--;
  return { lines, characters: value.length, long: lines > 6 || value.length > 480 };
}

export function yamlTextExcerpt(value: string, query = '') {
  const term = query.trim();
  // Escape the query so punctuation remains literal; regex match indices refer
  // to the original text even when Unicode case folding changes string length.
  const match = term ? new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'iu').exec(value) : null;
  const matchIndex = match?.index ?? -1;
  let start = 0;
  if (matchIndex > 0) {
    const lineStart = Math.max(value.lastIndexOf('\n', matchIndex - 1), value.lastIndexOf('\r', matchIndex - 1)) + 1;
    start = Math.max(lineStart, matchIndex - 40);
    // Avoid slicing a surrogate pair in a long single-line value.
    if (start > 0 && /[\uDC00-\uDFFF]/.test(value[start])) start++;
  }
  let end = Math.min(value.length, start + 240);
  let breaks = 0;
  for (let index = start; index < end; index++) {
    if (value[index] === '\r' || value[index] === '\n') {
      if (++breaks === 3) { end = index; break; }
      if (value[index] === '\r' && value[index + 1] === '\n') index++;
    }
  }
  if (end < value.length && /[\uDC00-\uDFFF]/.test(value[end])) end--;
  const text = `${start ? '…' : ''}${value.slice(start, end).trimEnd()}${end < value.length ? '…' : ''}`;
  return { text, matched: !!match };
}
