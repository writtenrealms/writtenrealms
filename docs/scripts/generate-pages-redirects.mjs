import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { redirectDocument } from "./redirects.mjs";

const docsDirectory = fileURLToPath(new URL("..", import.meta.url));
const buildDirectory = resolve(docsDirectory, ".vitepress/dist");
const outputDirectory = resolve(docsDirectory, ".vitepress/pages-redirects");

async function htmlFiles(directory, prefix = "") {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const name = prefix + entry.name;
    if (entry.isDirectory()) files.push(...await htmlFiles(resolve(directory, entry.name), name + "/"));
    else if (entry.name.endsWith(".html")) files.push(name);
  }
  return files;
}

const pages = await htmlFiles(buildDirectory);
if (!pages.includes("index.html")) throw new Error("Build the documentation before generating Pages redirects.");
await rm(outputDirectory, { recursive: true, force: true });

async function writeRedirect(path, document) {
  const output = resolve(outputDirectory, path);
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, document);
}

for (const page of pages) {
  if (page === "404.html") continue;
  const route = page.replace(/(^|\/)index\.html$/, "$1").replace(/\.html$/, "");
  const source = await readFile(resolve(buildDirectory, page), "utf8");
  // Legacy redirects already name the correct modern guide and section.
  const canonical = source.match(/<link rel="canonical" href="([^"]+)"/);
  const document = redirectDocument(canonical ? canonical[1] : route);
  await writeRedirect(page, document);
  if (!page.endsWith("/index.html") && page !== "index.html") {
    await writeRedirect(route + "/index.html", document);
  } else if (route) {
    await writeRedirect(route.replace(/\/$/, ".html"), document);
  }
}
await writeRedirect("404.html", redirectDocument("", { fallback: true }));
await writeRedirect(".nojekyll", "");
console.log(`Generated GitHub Pages redirects for ${pages.length - 1} documentation pages.`);
