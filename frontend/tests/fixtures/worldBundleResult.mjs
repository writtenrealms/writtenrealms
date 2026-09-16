export const worldBundleResult = {
  kind: "worldbundle",
  operation: "applied",
  summary: {
    documents: 11, content_documents: 10, worlds: 2, instances: 1, links: 3,
    kinds: { currency: 1, zone: 2, room: 2, itemdefinition: 1, world: 2, path: 1, trigger: 1 },
  },
  worlds: [
    { ref: "world@base", id: 12, name: "Phalanx" },
    { ref: "instance.outpost", id: 47, name: "Persian Outpost" },
  ],
  results: [
    { world_ref: "world@base", kind: "currency", operation: "created", currency: { id: 8, code: "obol", name: "Obol" } },
    { world_ref: "world@base", kind: "zone", operation: "updated", zone: { id: 100, ref: "zone@1", name: "The Academy" } },
    { world_ref: "world@base", kind: "room", operation: "updated", room: { id: 101, ref: "room@1", name: "Muster Yard" } },
    { world_ref: "world@base", kind: "itemdefinition", operation: "created", item_definition: { id: 400, name: "a training spear" } },
    { world_ref: "world@base", kind: "world", operation: "updated" },
    { world_ref: "instance.outpost", kind: "zone", operation: "updated", zone: { id: 200, ref: "zone@1", name: "The Outpost" } },
    { world_ref: "instance.outpost", kind: "room", operation: "updated", room: { id: 201, ref: "room@1", name: "Outpost Gate" } },
    { world_ref: "instance.outpost", kind: "path", operation: "created", path: { id: 31, name: "Guard Patrol", zone: { id: 200, ref: "zone@1" } } },
    { world_ref: "instance.outpost", kind: "trigger", operation: "created", trigger: { id: 88, name: "Gate Challenge", target: { type: "room", ref: "room@1" } } },
    { world_ref: "instance.outpost", kind: "world", operation: "updated" },
  ],
};
