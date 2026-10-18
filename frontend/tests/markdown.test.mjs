import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { renderMarkdown } = await server.ssrLoadModule('/src/core/markdown.ts');
const { default: MarkdownContent } = await server.ssrLoadModule('/src/components/MarkdownContent.vue');

test('renders document Markdown, tables, images, and code with the shared component', async () => {
  const source = '## Welcome\n\nA **bold** and *italic* world.\n\n- Explore\n- Talk\n\n> Advice\n\n[Guide](/docs/)\n\n![Map](https://example.com/map.png)\n\n| Place | Activity |\n| --- | --- |\n| Agora | Trade |\n\n```text\nlook\n```';
  const html = await renderToString(createSSRApp(MarkdownContent, { source }));
  for (const expected of ['class="markdown-content"', '<h2>Welcome</h2>', '<strong>bold</strong>', '<em>italic</em>', '<ul>', '<blockquote>', '<table>', '<pre>', 'href="/docs/"', 'alt="Map"']) {
    assert.ok(html.includes(expected), expected);
  }
});

test('builder content cannot execute HTML, Vue expressions, or unsafe URLs', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n[click](javascript:alert(1))\n\n[click](vbscript:evil)\n\n![x](data:image/svg+xml;base64,PHN2Zz4=)\n\n{{ window.location }}');
  assert.doesNotMatch(html, /<script|<img|href="(?:javascript|vbscript):|src="data:/i);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /\{\{ window.location \}\}/);
});

test('preserves Markdown hard breaks and indented code', () => {
  const html = renderMarkdown('One  \nTwo\n\n    look\n');
  assert.match(html, /One<br>\nTwo/);
  assert.match(html, /<pre><code>look\n<\/code><\/pre>/);
});
