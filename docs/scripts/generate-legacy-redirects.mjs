import { mkdir, writeFile } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const legacyRoutes = {
  "/playing": "/players/",
  "/playing/conduct": "https://writtenrealms.com/conduct",
  "/playing/communication": "/players/socials",
  "/playing/formulas": "/players/combat",
  "/playing/classes": "/players/combat",
  "/playing/items": "/players/",
  "/playing/quests": "/players/scripted-interactions",
  "/playing/experience": "/players/",
  "/building": "/builders/",
  "/building/worlds": "/builders/world-config-builder-guide",
  "/building/worlds/publishing": "/builders/world-publishing",
  "/building/commands": "/builders/builder-command-reference",
  "/building/conditions": "/builders/condition-builder-guide",
  "/building/factions": "/builders/faction-builder-guide",
  "/building/facts": "/builders/state-builder-guide",
  "/building/roomchecks": "/builders/condition-builder-guide#triggers",
  "/building/roomactions": "/builders/room-actions",
  "/building/itemactions": "/builders/trigger-builder-guide",
  "/building/quests": "/builders/quest-builder-guide",
  "/building/mobs": "/builders/mob-definition-builder-guide",
  "/building/mobs/items": "/builders/item-definition-builder-guide",
  "/building/loading": "/builders/spawn-plan-builder-guide",
  "/building/mobs/reactions": "/builders/trigger-builder-guide#other-trigger-shapes",
  "/building/moderation": "/builders/builder-command-reference",
  "/building/doors": "/builders/room-builder-guide#flags-details-and-doors-replace-their-lists",
  "/building/items": "/builders/item-definition-builder-guide",
  "/building/socials": "/builders/social-builder-guide",
  "/building/marks": "/builders/state-builder-guide",
  "/building/keywords": "/builders/builder-command-reference",
  "/building/incrementation": "/builders/state-builder-guide",
  "/building/randomization": "/builders/item-definition-builder-guide#random-stat-items",
  "/FAQ": "/",
  "/faq": "/",
};

const docsDirectory = fileURLToPath(new URL("..", import.meta.url));
const outputDirectory = resolve(docsDirectory, ".vitepress/dist");
const siteOrigin = "https://docs.writtenrealms.com";

const escapeHtml = (value) => value
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const redirectDocument = (destination) => {
  const canonicalUrl = new URL(destination, siteOrigin).href;
  const escapedUrl = escapeHtml(canonicalUrl);

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="0; url=${escapedUrl}">
    <link rel="canonical" href="${escapedUrl}">
    <title>Guide moved | Written Realms</title>
    <script>
      (() => {
        const target = new URL(${JSON.stringify(destination)}, window.location.origin);
        if (window.location.search) target.search = window.location.search;
        if (window.location.hash) target.hash = window.location.hash;
        window.location.replace(target.href);
      })();
    </script>
  </head>
  <body>
    <p>This guide moved to <a href="${escapedUrl}">${escapedUrl}</a>.</p>
  </body>
</html>
`;
};

for (const [legacyRoute, destination] of Object.entries(legacyRoutes)) {
  const routeSegments = legacyRoute.split("/").filter(Boolean);
  const routeDirectory = resolve(outputDirectory, ...routeSegments);
  if (!routeDirectory.startsWith(`${outputDirectory}${sep}`)) {
    throw new Error(`Legacy route escapes the docs output directory: ${legacyRoute}`);
  }
  await mkdir(routeDirectory, { recursive: true });
  await writeFile(
    resolve(routeDirectory, "index.html"),
    redirectDocument(destination),
    "utf8",
  );
}

console.log(`Generated ${Object.keys(legacyRoutes).length} legacy documentation redirects.`);
