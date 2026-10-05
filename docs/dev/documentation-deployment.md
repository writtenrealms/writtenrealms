# Documentation deployment

The canonical WR2 builder and player guides are at
[core.writtenrealms.com/docs/](https://core.writtenrealms.com/docs/).
VitePress builds only `docs/guides/`; engineering and architecture notes outside
that directory are not part of the published site.

Core's web image builds the guides from the same Git revision as the application
and serves the static output through Caddy. Documentation requests need no
Django, database, or worker processing. Guide changes become live with a Core
release, so the public manual follows the deployed game rather than unreleased
changes on `main`.

## Local verification

```bash
make docs-install
npm --prefix docs test
make docs-build
npm --prefix docs run preview
```

Open `http://localhost:4173/docs/` for the production preview. `make docs`
instead runs the development server at `http://localhost:5174/docs/`.
The `/docs/` base is configured in `docs/.vitepress/config.mts`; the production
output is `docs/.vitepress/dist/`.

Check the home page, a builder guide, a player guide, search, and a section
bookmark. Styles, scripts, links, and search results must all stay under
`/docs/`. The host must resolve clean guide URLs to their generated `.html`
files, redirect `/docs` to `/docs/`, and return 404 for missing docs rather than
fall through to the game frontend.

## GitHub Pages redirects

[writtenrealms.github.io/writtenrealms/](https://writtenrealms.github.io/writtenrealms/)
contains redirects to the canonical Core site. It does not host a second copy
of the guides. The Pages custom domain must remain unset; select **GitHub
Actions** as the Pages source and leave HTTPS enforcement enabled.

The `.github/workflows/docs.yml` workflow tests and builds the docs, then runs:

```bash
npm --prefix docs run build:redirects
```

Only `docs/.vitepress/pages-redirects/` is uploaded to GitHub Pages. The generator
creates redirects for every built guide and legacy route, including clean,
`.html`, and directory forms. A custom `404.html` forwards future guide paths
that are not yet represented in the redirect artifact. Query strings and
section bookmarks are preserved by JavaScript; a meta refresh and visible
canonical link also work without JavaScript.

Verify redirects from the Pages home, a nested builder page with `#section`,
a player guide, and a legacy route such as `/building/conditions`.
The workflow runs on docs changes to `main` and supports manual dispatch.
Publishing new redirects does not release new guide content on Core; links to
newer guides become usable when Core is updated to include them.

## WR1/Alpha stays separate

[docs.writtenrealms.com](https://docs.writtenrealms.com/) remains the WR1/Alpha
manual. Core deployment does not change that domain, its DNS, or its hosting.
The compatibility routes generated inside the Core docs and Pages redirect
site point to the corresponding WR2 guides; they do not replace the Alpha site.

## Release and recovery

Use the private Core operations runbook and deployment helper for production
releases. Deploy and verify Core's `/docs/` before publishing Pages redirects
to it. Preserve the previous release and follow the same compatibility checks
as an application rollback. A rollback to a release predating `/docs/` removes
the redirect destination, so recover the docs route or retain a docs-enabled
release before rolling back.
