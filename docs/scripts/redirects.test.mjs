import assert from "node:assert/strict";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { redirectDocument } from "./redirects.mjs";

function redirectedTo(destination, source, options) {
  const document = redirectDocument(destination, options);
  const script = document.match(/<script>([\s\S]*?)<\/script>/)[1];
  let actual;
  const url = new URL(source);
  runInNewContext(script, {
    URL,
    window: { location: {
      pathname: url.pathname, search: url.search, hash: url.hash,
      replace(value) { actual = value; },
    } },
  });
  return actual;
}

test("guide redirects preserve queries and section bookmarks", () => {
  assert.equal(redirectedTo("builders/condition-builder-guide", "https://writtenrealms.github.io/writtenrealms/builders/condition-builder-guide?from=bookmark#triggers"),
    "https://core.writtenrealms.com/docs/builders/condition-builder-guide?from=bookmark#triggers");
});

test("legacy redirects keep their mapped section unless the bookmark supplies one", () => {
  const destination = "/builders/condition-builder-guide#triggers";
  assert.equal(redirectedTo(destination, "https://core.writtenrealms.com/docs/building/roomchecks"),
    "https://core.writtenrealms.com/docs/builders/condition-builder-guide#triggers");
  assert.equal(redirectedTo(destination, "https://writtenrealms.github.io/writtenrealms/building/roomchecks#custom"),
    "https://core.writtenrealms.com/docs/builders/condition-builder-guide#custom");
});

test("external legacy destinations remain external", () => {
  assert.equal(redirectedTo("https://writtenrealms.com/conduct", "https://writtenrealms.github.io/writtenrealms/playing/conduct"),
    "https://writtenrealms.com/conduct");
});

test("Pages 404 fallback handles new guides and old .html bookmarks", () => {
  for (const [path, route] of [
    ["/writtenrealms/players/new-guide.html", "players/new-guide"],
    ["/writtenrealms/builders/index.html", "builders/"],
    ["/writtenrealms/", ""],
    ["/writtenrealms", ""],
    ["/unrelated", ""],
    ["/writtenrealms/https://example.com/", "https://example.com/"],
  ]) {
    assert.equal(redirectedTo("", `https://writtenrealms.github.io${path}?q=help#section`, { fallback: true }),
      `https://core.writtenrealms.com/docs/${route}?q=help#section`);
  }
});

test("redirect HTML offers the canonical destination without JavaScript", () => {
  const html = redirectDocument("players/combat");
  assert.match(html, /http-equiv="refresh" content="0; url=https:\/\/core\.writtenrealms\.com\/docs\/players\/combat"/);
  assert.match(html, /<a href="https:\/\/core\.writtenrealms\.com\/docs\/players\/combat">/);
});
