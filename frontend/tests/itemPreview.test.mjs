import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";
import { createSSRApp, defineComponent } from "vue";
import { createStore } from "vuex";
import { renderToString } from "vue/server-renderer";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
  optimizeDeps: { noDiscovery: true, include: [] },
});
after(() => server.close());
const { useItemPreview, itemPreviewPosition } = await server.ssrLoadModule("/src/composables/useItemPreview.ts");
const { default: ItemPreview } = await server.ssrLoadModule("/src/components/ui/ItemPreview.vue");

const item = {
  key: "item.1", name: "a bronze spear", type: "equippable", equipment_type: "weapon_1h",
  weapon_type: "spear", quality: "imbued", level: 5, weapon_damage: 12,
  description: "A bronze blade caps an ash shaft.", attributes: { strength: 2 },
};
const anchor = { matches: () => true };
const pointer = (pointerType = "mouse") => ({ pointerType, currentTarget: anchor });
async function controller() {
  let result;
  await renderToString(createSSRApp(defineComponent({
    setup() { result = useItemPreview(); return () => null; },
  })));
  return result;
}

test("one preview follows the current hovered item and exposes its expanded state", async () => {
  const state = await controller();
  state.bindings(item).onPointerenter(pointer());
  assert.equal(state.preview.value.item, item);
  assert.equal(state.preview.value.anchor, anchor);
  assert.equal(state.preview.value.centered, false);
  assert.equal(state.bindings(item)["aria-expanded"], true);
  const next = { ...item, key: "item.2" };
  state.bindings(next).onPointerenter(pointer());
  assert.equal(state.preview.value.item, next);
  assert.equal(state.bindings(item)["aria-expanded"], false);
  state.close();
  assert.equal(state.preview.value, null);
});

test("touch opens only on tap, stays centered on leave, and ignores background hover", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = await controller();
  state.bindings(item).onPointerenter(pointer("touch"));
  assert.equal(state.preview.value, null);
  state.bindings(item).onClick(pointer("touch"));
  assert.equal(state.preview.value.centered, true);
  assert.equal(state.preview.value.focus, true);
  state.leave();
  t.mock.timers.tick(200);
  state.bindings({ ...item, key: "item.2" }).onPointerenter(pointer());
  state.bindings(item).onFocus({ currentTarget: anchor });
  assert.equal(state.preview.value.item, item);
  assert.equal(state.preview.value.centered, true);
  state.close();
});

test("mobile mouse emulation skips hover so the preview cannot intercept the tap", async (t) => {
  const originalWindow = globalThis.window;
  globalThis.window = { matchMedia: () => ({ matches: true }) };
  t.after(() => { globalThis.window = originalWindow; });
  const state = await controller();
  state.bindings(item).onPointerenter(pointer());
  assert.equal(state.preview.value, null);
  state.bindings(item).onClick(pointer());
  assert.equal(state.preview.value.centered, true);
  state.close();
});

test("keyboard focus previews an item and moving into the popup cancels dismissal", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = await controller();
  state.bindings(item).onFocus({ currentTarget: anchor });
  assert.equal(state.preview.value.item, item);
  state.leave();
  t.mock.timers.tick(100);
  state.keepOpen();
  t.mock.timers.tick(200);
  assert.equal(state.preview.value.item, item);
  state.leave();
  t.mock.timers.tick(150);
  assert.equal(state.preview.value, null);
});

test("desktop positioning flips at the right edge and keeps tall previews on screen", () => {
  const viewport = { width: 1000, height: 700 };
  const panel = { width: 324, height: 400 };
  assert.deepEqual(itemPreviewPosition({ left: 100, right: 300, top: 100, bottom: 120 }, panel, viewport),
    { left: "312px", top: "100px" });
  assert.deepEqual(itemPreviewPosition({ left: 800, right: 950, top: 600, bottom: 620 }, panel, viewport),
    { left: "464px", top: "288px" });
  assert.deepEqual(itemPreviewPosition({ left: 300, right: 500, top: 100, bottom: 120 }, panel, { width: 780, height: 700 }),
    { left: "300px", top: "132px" });
  assert.deepEqual(itemPreviewPosition({ left: 300, right: 500, top: 600, bottom: 620 }, panel, { width: 780, height: 700 }),
    { left: "300px", top: "188px" });
});

test("previews use the supplied character for comparisons and the game's item formatting", async () => {
  // Conflicting live-game state must not leak into another character's page.
  const store = createStore({ state: { game: { player: { level: 1 }, world: {} } } });
  const context = {};
  await renderToString(createSSRApp(ItemPreview, {
    preview: { item, anchor, centered: true, focus: true },
    player: { level: 5, equipment: { weapon: { weapon_damage: 8 } }, marks: {} },
    world: { allow_combat: true },
  }).use(store), context);
  const html = context.teleports.body;
  assert.match(html, /<dialog[^>]*lookup-item lookup-mobile centered/);
  assert.match(html, /aria-label="a bronze spear details"/);
  assert.match(html, /Close item details/);
  assert.match(html, /A bronze blade caps an ash shaft\./);
  assert.match(html, /\(\+4\)/);
  assert.doesNotMatch(html, /Can only wear items/);
});

test("equipment, inventory, and bag contents all open previews without a sidebar detail box", async () => {
  const source = await readFile(new URL("../src/views/lobby/CharacterPage.vue", import.meta.url), "utf8");
  for (const item of ["slot.item", "item", "content"]) {
    assert.ok(source.includes(`v-bind="itemPreviewBindings(${item})"`));
  }
  assert.equal((source.match(/<ItemPreview /g) || []).length, 1);
  assert.doesNotMatch(source, /item-detail|Select an item to see its details/);
  assert.match(source, /watch\(data, closePreview\)/);
});
