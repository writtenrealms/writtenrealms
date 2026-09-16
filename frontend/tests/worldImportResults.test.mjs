import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import axios from "axios";
import { createServer } from "vite";
import { createSSRApp, onServerPrefetch } from "vue";
import { createStore } from "vuex";
import { createMemoryHistory, createRouter } from "vue-router";
import { renderToString } from "vue/server-renderer";
import { worldBundleResult } from "./fixtures/worldBundleResult.mjs";

const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { middlewareMode: true, watch: null, hmr: false },
});
after(() => server.close());
const { default: EditWorld } = await server.ssrLoadModule("/src/views/builder/world/EditWorld.vue");

const harness = async (data, { builderRank = 4, exercise } = {}) => {
  const notifications = [];
  const refreshes = [];
  const store = createStore({
    state: { builder: { world: { id: 12, name: "Destination", builder_info: { builder_rank: builderRank } } } },
    mutations: {
      "ui/notification_set": (_, text) => notifications.push(text),
      "ui/notification_set_error": (_, text) => notifications.push(`ERROR: ${text}`),
    },
    actions: Object.fromEntries([
      "builder/fetch_world", "builder/worlds/config_fetch", "builder/fetch_world_map",
    ].map(name => [name, (_, payload) => { refreshes.push({ name, payload }); return {}; }])),
  });
  const router = createRouter({
    history: createMemoryHistory(),
    routes: Object.entries({
      builder_world_edit: "edit",
      builder_world_config: "config",
      builder_world_currency_list: "currencies",
      builder_zone_index: "zones/:zone_relative_id",
      builder_room_index: "rooms/:room_relative_id",
      builder_zone_path_details: "zones/:zone_relative_id/paths/:path_id",
      builder_room_trigger_details: "rooms/:room_relative_id/triggers/:trigger_id",
      builder_world_trigger_details: "triggers/:trigger_id",
      builder_item_definition_details: "items/:item_definition_id",
      builder_world_ability_details: "abilities/:ability_id",
      builder_world_social_list: "socials",
    }).map(([name, path]) => ({ name, path: `/build/worlds/:world_id/${path}`, component: { render: () => null } })),
  });
  await router.push("/build/worlds/12/edit");
  let bindings;
  const component = {
    ...EditWorld,
    setup(props, context) {
      bindings = EditWorld.setup(props, context);
      if (data) bindings.setAppliedResult(structuredClone(data));
      // Exercise handlers before SSR disposes the component's reactive scope.
      onServerPrefetch(() => exercise?.({ bindings, notifications, refreshes }));
      return bindings;
    },
  };
  const html = await renderToString(createSSRApp(component).use(store).use(router));
  return { html, bindings, notifications, refreshes, router };
};

test("family results show document, world, instance and link counts with named world groups", async () => {
  const { html, bindings } = await harness(worldBundleResult);
  assert.match(html, /World Family Applied/);
  assert.match(html, /11 documents across 2 worlds \(1 instance\)/);
  assert.match(html, /3 cross-world links/);
  assert.match(html, /1 currency/);
  assert.match(html, /Phalanx/);
  assert.match(html, /Persian Outpost/);
  assert.doesNotMatch(html, /No linkable entities/);
  assert.equal(bindings.appliedEntities.value.length, 10);
  assert.deepEqual(bindings.appliedEntityGroups.value.map(group => group.entities.length), [5, 5]);
});

test("identical relative references link to their imported world, including paths and triggers", async () => {
  const { html, bindings, router } = await harness(worldBundleResult);
  const expected = {
    "Muster Yard": "/build/worlds/12/rooms/1",
    "Outpost Gate": "/build/worlds/47/rooms/1",
    "The Academy": "/build/worlds/12/zones/1",
    "The Outpost": "/build/worlds/47/zones/1",
    "Guard Patrol": "/build/worlds/47/zones/1/paths/31",
    "Gate Challenge": "/build/worlds/47/rooms/1/triggers/88",
    "Phalanx": "/build/worlds/12/config",
    "Persian Outpost": "/build/worlds/47/config",
    "a training spear": "/build/worlds/12/items/400",
  };
  for (const [name, href] of Object.entries(expected)) {
    const entity = bindings.appliedEntities.value.find(entity => entity.name === name);
    assert.ok(entity, name);
    assert.equal(router.resolve(entity.to).href, href, name);
    assert.ok(html.includes(`href="${href}"`), href);
  }
  const keys = bindings.appliedEntities.value.map(entity => entity.key);
  assert.equal(new Set(keys).size, keys.length);
});

test("unresolved family scopes remain visible without misleading base-world links", async () => {
  const data = structuredClone(worldBundleResult);
  data.worlds = data.worlds.slice(0, 1);
  const { bindings, html } = await harness(data);
  const instanceEntities = bindings.appliedEntities.value.slice(5);
  assert.equal(instanceEntities.length, 5);
  assert.ok(instanceEntities.every(entity => !entity.to));
  assert.match(html, /Outpost Gate/);
  assert.match(html, /instance.outpost/);
});

test("ordinary batches retain current-world routing, ability expansion and deleted rows", async () => {
  const { bindings, html } = await harness({
    kind: "batch", operation: "applied", summary: { documents: 4, kinds: { abilities: 1, room: 2, social: 1 } },
    results: [
      { kind: "abilities", operation: "created", abilities: [{ id: 5, name: "Strike" }, { id: 6, name: "Guard" }] },
      { kind: "room", operation: "updated", room: { id: 101, ref: "room@1", name: "Yard" } },
      { kind: "room", operation: "deleted", room: { id: 102, ref: "room@2", name: "Old Room" } },
      { kind: "social", operation: "created", social: { id: 9, cmd: "salute" } },
    ],
  });
  assert.match(html, /Applied 4 documents/);
  assert.equal(bindings.appliedEntities.value.length, 5);
  assert.ok(bindings.appliedEntities.value.slice(0, 3).every(entity => entity.to.params.world_id === "12"));
  assert.equal(bindings.appliedEntities.value[3].to, undefined);
  assert.match(html, /Old Room/);
  assert.equal(bindings.appliedEntities.value[4].name, "salute");
  assert.match(html, /href="\/build\/worlds\/12\/socials"/);
});

test("single results and applying another manifest clear family state", async () => {
  const { bindings } = await harness(worldBundleResult, { exercise: ({ bindings }) => {
    bindings.startAnotherManifest();
    assert.equal(bindings.hasApplyResult.value, false);
    assert.equal(bindings.manifestText.value, "");
    assert.equal(bindings.appliedEntities.value.length, 0);
    assert.equal(bindings.appliedWorlds.value.length, 0);
    bindings.setAppliedResult({ kind: "world", operation: "updated" });
  } });
  assert.equal(bindings.appliedSummaryText.value, 'Updated world config "Destination".');
  assert.equal(bindings.appliedEntities.value[0].to.params.world_id, "12");
});

test("successful family submission sends a readable notification and refreshes only the selected world", async () => {
  const adapter = axios.defaults.adapter;
  axios.defaults.adapter = async config => ({ data: worldBundleResult, status: 200, statusText: "OK", headers: {}, config });
  let result;
  try {
    result = await harness(undefined, { exercise: async ({ bindings }) => {
      bindings.manifestText.value = "kind: worldbundle";
      await bindings.submitManifest();
    } });
  } finally {
    axios.defaults.adapter = adapter;
  }
  const { bindings, notifications, refreshes } = result;
  assert.deepEqual(notifications, ["Applied 11 documents across 2 worlds."]);
  assert.equal(bindings.hasApplyResult.value, true);
  assert.equal(bindings.isSubmitting.value, false);
  assert.deepEqual(refreshes.map(entry => entry.payload), ["12", { world_id: "12" }, "12"]);
});
