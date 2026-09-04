import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null },
});
after(() => server.close());
const { default: AbilityCastMessage } = await server.ssrLoadModule(
  "/src/components/game/console/AbilityCastMessage.vue",
);

const renderMessage = (text, name = "Crush") => renderToString(createSSRApp(
  AbilityCastMessage,
  { message: { text, data: { ability: { name } } } },
));

const emphasizedText = (html) => Array.from(
  html.matchAll(/<strong class="color-primary"[^>]*>(.*?)<\/strong>/g),
  (match) => match[1],
);

const plainText = (html) => html.replace(/<[^>]*>/g, "");

test("mob charge and continued charge emphasize the action and ability", async () => {
  for (const verb of ["charges", "continues charging"]) {
    const text = `Tigranes the spear-bearer ${verb} Crush.`;
    const html = await renderMessage(text);
    assert.deepEqual(emphasizedText(html), [verb, "Crush"]);
    assert.equal(plainText(html), text);
  }
});

test("a matching mob name stays plain and multiword abilities stay together", async () => {
  const html = await renderMessage("Crushing Blow charges Crushing Blow.", "Crushing Blow");
  assert.deepEqual(emphasizedText(html), ["charges", "Crushing Blow"]);
  assert.equal(plainText(html), "Crushing Blow charges Crushing Blow.");
});

test("authored ability names render as text, without interpreting HTML", async () => {
  const html = await renderMessage("Tigranes charges <Crush & Burn>.", "<Crush & Burn>");
  assert.deepEqual(emphasizedText(html), ["charges", "&lt;Crush &amp; Burn&gt;"]);
  assert.doesNotMatch(html, /<Crush/);
});

test("missing or mismatched ability metadata preserves the plain message", async () => {
  for (const name of [null, "", "Bash"]) {
    const html = await renderMessage("Tigranes charges Crush.", name);
    assert.match(html, />Tigranes charges Crush\.</);
    assert.doesNotMatch(html, /<strong/);
  }
});

test("interrupts emphasize the action and canceled skill in both perspectives", async () => {
  for (const [text, verb] of [
    ["You interrupt Tigranes the spear-bearer's cast of Crush.", "interrupt"],
    ["Tigranes the spear-bearer interrupts your cast of Crush.", "interrupts"],
    ["You interrupt Tigranes the spear-bearer's channel of Crush.", "interrupt"],
    ["You interrupt the interrupts keeper's cast of Crush.", "interrupt"],
    ["You interrupt the keeper interrupts your cast of Crush.", "interrupts"],
  ]) {
    const html = await renderToString(createSSRApp(AbilityCastMessage, {
      message: {
        type: "notification.combat.ability_interrupted",
        text,
        data: {
          ability: { name: "Kick" },
          interrupted_ability: { name: "Crush" },
        },
      },
    }));
    assert.deepEqual(emphasizedText(html), [verb, "Crush"]);
    assert.equal(plainText(html).replaceAll("&#39;", "'"), text);
    assert.doesNotMatch(html, /Kick/);
  }
});

test("charging words inside actor and ability names are not mistaken for actions", async () => {
  const text = "The beast that charges continues charging Last interrupt.";
  const html = await renderMessage(text, "Last interrupt");
  assert.deepEqual(emphasizedText(html), ["continues charging", "Last interrupt"]);
  assert.equal(plainText(html), text);
});

test("interrupts without a canceled name keep the original message plain", async () => {
  const html = await renderToString(createSSRApp(AbilityCastMessage, {
    message: {
      type: "notification.combat.ability_interrupted",
      text: "You interrupt Tigranes's cast.",
      data: { ability: { name: "Kick" }, interrupted_ability: { slug: "crush" } },
    },
  }));
  assert.match(html, />You interrupt Tigranes&#39;s cast\.</);
  assert.doesNotMatch(html, /<strong/);
});
