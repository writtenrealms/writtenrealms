import { copyFileSync, cpSync, existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';

const allowed = new Set(['.md', '.mdinc', '.png', '.jpg', '.jpeg', '.webp', '.svg']);
const route = (file) => file.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '');
const text = (value, name) => {
  if (typeof value !== 'string' || !value.trim() || /[\r\n<>]/.test(value)) throw new Error(`Invalid ${name}`);
  return value;
};

export function safeFile(root, name) {
  if (typeof name !== 'string' || !name || isAbsolute(name) || name.includes('\\') || name.split('/').some(p => !p || p === '..' || p.startsWith('.'))) {
    throw new Error(`Unsafe guide path: ${name}`);
  }
  const path = resolve(root, name);
  // Reject symlinks, including directories, even when the target stays inside root.
  let part = root;
  for (const segment of name.split('/')) {
    part = join(part, segment);
    if (lstatSync(part).isSymbolicLink()) throw new Error(`Guide symlink is not allowed: ${name}`);
  }
  if (!realpathSync(path).startsWith(realpathSync(root) + sep) || !lstatSync(path).isFile()) throw new Error(`Not a guide file: ${name}`);
  return path;
}

export function readGuide(manifestPath) {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  if (manifest.schemaVersion !== 1) throw new Error('Unsupported world guide schemaVersion');
  if (!/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(manifest.slug)) throw new Error('Invalid world guide slug');
  text(manifest.title, 'guide title');
  text(manifest.description, 'guide description');
  // The public tree is deliberately fixed and cannot point at a parent directory.
  const root = join(dirname(resolve(manifestPath)), 'guide');
  if (lstatSync(root).isSymbolicLink()) throw new Error('Guide root cannot be a symlink');
  if (!Array.isArray(manifest.files) || !manifest.files.includes('index.md') || new Set(manifest.files).size !== manifest.files.length) throw new Error('Guide files must be unique and include index.md');
  const routes = new Set();
  for (const file of manifest.files) {
    if (!allowed.has(extname(file))) throw new Error(`Unsupported guide file: ${file}`);
    const path = safeFile(root, file);
    if (file.endsWith('.md')) {
      const url = route(file);
      if (routes.has(url)) throw new Error(`Duplicate guide route: ${url}`);
      routes.add(url);
    }
    if (file.endsWith('.md') || file.endsWith('.mdinc')) {
      const source = readFileSync(path, 'utf8');
      for (const match of source.matchAll(/<!--\s*@include:\s*([^>]+?)\s*-->/g)) {
        const included = relative(root, resolve(dirname(path), match[1])).split(sep).join('/');
        if (!manifest.files.includes(included)) throw new Error(`Include is not published: ${match[1]}`);
        safeFile(root, included);
      }
    }
  }
  const prefix = `/worlds/${manifest.slug}/`;
  function sidebar(items, depth = 0) {
    if (!Array.isArray(items) || depth > 4) throw new Error('Invalid guide sidebar');
    return items.map(item => {
      const result = { text: text(item.text, 'sidebar text') };
      if (item.link !== undefined) {
        if (typeof item.link !== 'string' || !routes.has(item.link)) throw new Error(`Sidebar page is not published: ${item.link}`);
        result.link = prefix + item.link;
      }
      if (item.items) result.items = sidebar(item.items, depth + 1);
      if (!result.link && !result.items) throw new Error('Sidebar item needs a link or children');
      return result;
    });
  }
  return { ...manifest, root, prefix, sidebar: sidebar(manifest.sidebar) };
}

export function prepareWorldGuides(docsRoot, encoded = process.env.DOCS_WORLD_GUIDES) {
  if (!encoded) return { srcDir: 'guides', nav: [], sidebar: {}, extended: false };
  const paths = JSON.parse(encoded);
  if (!Array.isArray(paths) || !paths.length || paths.some(p => typeof p !== 'string' || !isAbsolute(p))) throw new Error('DOCS_WORLD_GUIDES must be a JSON array of absolute manifest paths');
  const guides = paths.map(readGuide);
  if (new Set(guides.map(g => g.slug)).size !== guides.length) throw new Error('Duplicate world guide slug');
  const source = join(docsRoot, 'guides');
  if (existsSync(join(source, 'worlds'))) throw new Error('The worlds/ route is reserved for external guides');
  const destination = join(docsRoot, '.vitepress/assembled');
  rmSync(destination, { recursive: true, force: true });
  cpSync(source, destination, { recursive: true });
  const sidebar = {};
  for (const guide of guides) {
    const target = join(destination, 'worlds', guide.slug);
    for (const file of guide.files) {
      mkdirSync(dirname(join(target, file)), { recursive: true });
      copyFileSync(safeFile(guide.root, file), join(target, file));
    }
    sidebar[guide.prefix] = guide.sidebar;
  }
  const escape = value => value.replace(/[\\`*_[\]{}]/g, '\\$&');
  writeFileSync(join(destination, 'worlds/index.md'), '# World guides\n\nExplore the worlds of Written Realms.\n\n' + guides.map(g => `- [${escape(g.title)}](${g.prefix}) — ${escape(g.description)}\n`).join(''));
  sidebar['/worlds/'] = [{ text: 'World guides', link: '/worlds/' }, ...guides.map(g => ({ text: g.title, link: g.prefix }))];
  return { srcDir: '.vitepress/assembled', nav: [{ text: 'Worlds', link: '/worlds/' }], sidebar, extended: true };
}

// A newer renderer may compose an older deployed manual. Keep its standard
// navigation limited to that manual; external guide navigation is strict above.
export function existingSidebar(items, sourceDirectory) {
  return items.flatMap(item => {
    const result = { ...item };
    if (result.items) result.items = existingSidebar(result.items, sourceDirectory);
    if (result.link?.startsWith('/')) {
      const path = result.link.split('#')[0];
      const file = join(sourceDirectory, path.slice(1) + (path.endsWith('/') ? 'index.md' : '.md'));
      if (!existsSync(file)) return [];
    }
    if (!result.link && !result.items?.length) return [];
    return [result];
  });
}
