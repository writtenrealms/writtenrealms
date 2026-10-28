# External world guides

Core can compose optional world guides into its VitePress site at
`/docs/worlds/<slug>/`. The standard build has no external dependencies and
publishes the ordinary player, builder, and administrator manual.

## Contract version 1

A guide has a JSON manifest next to a `guide/` directory:

```json
{
  "schemaVersion": 1,
  "slug": "example",
  "title": "Example World",
  "description": "Player information for Example World.",
  "files": ["index.md", "classes/scout.md", "reference/stats.mdinc"],
  "sidebar": [
    { "text": "Overview", "link": "" },
    { "text": "Classes", "items": [{ "text": "Scout", "link": "classes/scout" }] }
  ]
}
```

`files` explicitly selects publishable inputs below `guide/`. Markdown pages,
`.mdinc` include fragments, PNG/JPEG/WebP images, and SVG assets are supported.
Private notes, unlisted files, symlinks, traversal paths, and executable module
files are excluded or rejected. Markdown includes must resolve to an explicitly
listed file. Guide content is trusted maintainer-authored Markdown, not a
sandbox for arbitrary public uploads: VitePress supports HTML and Vue syntax.

Sidebar links are guide-relative routes, without `.md`, a leading slash, or the
`/docs/` base. `""` selects `index.md`; `"classes/"` selects
`classes/index.md`. Duplicate guide slugs and the reserved Core `worlds/`
directory are rejected. The build validates sidebar targets; VitePress's strict
link check validates page links. Use stable slugs for public URLs.

## Build and preview

Supply absolute manifest paths as a JSON array:

```sh
DOCS_WORLD_GUIDES='["/absolute/path/to/example/guide.json"]' make docs-build
npm --prefix docs run preview
```

The build copies the standard guides and only the selected external files into
ignored `docs/.vitepress/assembled/`. It never edits the original guide sources.
The combined site has a Worlds index, a sidebar for each world, and one local
search index. External pages suppress Core's GitHub edit link and Git-derived
last-updated timestamp. Put a verified-data date in generated references instead.

For development, set the same environment variable with `make docs`. Restart
the dev server after editing an external source or its manifest to reassemble.
Do not run concurrent builds against the same docs directory; use isolated
build directories for automated publishing.

Without `DOCS_WORLD_GUIDES`, `make docs-build` publishes only `docs/guides/`.
A publisher that builds from Git archives must explicitly supply the external
content; ignored local directories do not enter release archives.

## Ownership and publishing

Keep a world's actual content in its owning repository. The Core repository
contains the format and loader, not any particular world's names or mechanics.
Generate exact reference values from a reviewed snapshot outside the renderer;
use `.mdinc` fragments inside human/agent-authored Markdown so refreshes preserve
prose. The loader does not contact a game database or run an LLM.

An installation may publish a combined static artifact independently of a game
release. Pin the standard manual to the deployed game revision, record renderer
and content revisions separately, and reject activation if the game changed
while building. Preserve legacy URLs and deploy the complete search/assets/page
artifact together. Core documentation reads add no Django, database, worker,
or per-player work.

Verification: `npm --prefix docs test`, an ordinary `make docs-build`, a combined
build with a sample guide, and browser checks for links, mobile navigation,
search, light/dark themes, and section bookmarks.
