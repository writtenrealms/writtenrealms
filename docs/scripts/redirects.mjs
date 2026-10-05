export const canonicalDocsUrl = "https://core.writtenrealms.com/docs/";
export const pagesBase = "/writtenrealms";

export const guideUrl = (route) => new URL(route.replace(/^\/+/, ""), canonicalDocsUrl).href;

const escapeHtml = (value) => value
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

// Shared by normal, legacy, and GitHub Pages fallback redirects. The fallback
// handles bookmarks to guides added after this redirect artifact was built.
export function redirectDocument(destination, { fallback = false } = {}) {
  const canonicalUrl = guideUrl(destination);
  const escapedUrl = escapeHtml(canonicalUrl);
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="0; url=${escapedUrl}">
    <link rel="canonical" href="${escapedUrl}">
    <title>Guide moved | Written Realms Core</title>
    <script>
      (() => {
        let destination = ${JSON.stringify(canonicalUrl)};
        if (${fallback}) {
          const base = ${JSON.stringify(pagesBase)};
          const path = window.location.pathname;
          if (path.startsWith(base + "/")) {
            const route = path.slice(base.length + 1).replace(/^\\/+/, "")
              .replace(/(^|\\/)index\\.html$/, "$1").replace(/\\.html$/, "");
            const fallbackTarget = new URL(${JSON.stringify(canonicalDocsUrl)});
            fallbackTarget.pathname += route;
            destination = fallbackTarget.href;
          }
        }
        const target = new URL(destination);
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
}
