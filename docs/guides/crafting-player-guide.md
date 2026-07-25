# Crafting Player Guide

Crafting turns salvaged equipment and captured spoils into a chosen piece of
gear. The chosen item is guaranteed; its authored stat ranges roll once when it
is created.

## Commands

- `materials` lists current, unspent crafting material balances. It works
  anywhere and does not list recipes, inventory, currencies, or lifetime totals.
- `recipes [filter]` lists numbered recipes offered at the current workshop.
  Filters may include builder-authored recipe groups as well as `armor`,
  `weapons`, and `ready`. A recipe's currency fee and any missing amount appear
  alongside its material readiness.
- Bare `recipe` and bare `craft` show that same numbered catalog.
- Rooms configured as workshops display **CRAFT** and **SALVAGE** room actions.
  **CRAFT** runs bare `craft` and opens the same numbered recipe catalog.
  **SALVAGE** runs bare `salvage` and opens the read-only numbered list of
  currently eligible carried items.
- `recipe <number>` shows the corresponding output's fixed values, stat ranges,
  material requirements, currency fee, owned quantities, and anything missing.
  Item names still work.
- `craft <number>` spends the materials and currency fee atomically, then
  creates the corresponding item. Item names still work here too.
- `salvage` lists the carried items that can currently be salvaged, numbered
  from 1. The list is read-only and changes nothing.
- `salvage <number>` permanently destroys the corresponding item from the
  current numbered list and adds its fixed material yield.
- `salvage <item>` does the same using an item name. Numbered name selectors
  such as `salvage 2.helm` remain available when useful.
- `salvage spoils` processes up to 100 carried items explicitly marked as
  salvage-only and reports if more remain.
- Looking at an item, including through the item lookup window, displays
  **SALVAGEABLE** when its definition has authored salvage yields. This marks
  the item's salvage capability, not whether it is immediately eligible for
  the `salvage` command.

Crafting requires a workshop in the current room or an available crafting NPC.
`materials` and ordinary salvage do not require one. The workshop
**SALVAGE** action is a convenience; the `salvage` command remains available
anywhere.

Recipe numbers follow the authored order across all local workshops. Filters
and workshop-specific views retain those canonical numbers, so a narrowed list
may contain gaps: if it displays entries 2 and 5, `recipe 5` and `craft 5`
still select the latter. Use `at <workshop>` when several workshops offer the
same recipe or when the provider itself matters. Recipe numbers are convenient
catalog positions rather than permanent identifiers; list again after workshop
content or availability changes.

Inspection does not require choosing between workshops that offer the same
recipe. Crafting does: use `craft <number> at <workshop>` when prompted.

The `ready` label means every material, currency, and non-material condition is
currently satisfied. Recipe inspection and listing do not reserve anything;
the complete requirements are checked again when `craft` runs. If the balance
changed or any requirement fails, no material or currency is spent and no item
is created. Retrying a successfully processed craft request cannot charge the
fee or consume the materials a second time.

Equipped items, quest items, nonempty containers, items without salvage data,
and ambiguous item selections are rejected. Rolled item stats never change the
salvage yield. Materials persist through logout and death but cannot initially
be dropped, sold, or traded directly.

The numbered list includes at most 100 eligible items in inventory order. Its
numbers are not permanent item identifiers: picking up, dropping, equipping,
or salvaging gear may change them, so run `salvage` again when in doubt.

History replay must be issued as a standalone command. `!N` may replay a
stored semicolon-separated command chain, but it cannot be embedded inside a
new chain.
