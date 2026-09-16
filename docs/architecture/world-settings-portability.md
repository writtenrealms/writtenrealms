# World Settings Portability Audit

Reviewed September 16, 2026. This inventory covers all 50 fields declared on
`WorldConfig`, plus the surrounding world metadata that matters when moving
authored content between instances. It distinguishes gameplay configuration
from destination operation and live state. It does not define a WR1 database
migration or a live-world synchronization protocol.

## Gameplay Rules Restored By This Change

Three active rules previously had no world manifest representation:

| Field | Manifest scope | Runtime consumer |
| --- | --- | --- |
| `never_reload` | Each base or instance world owns its value. | `worlds.tasks.run_world_spawn_plans` skips scheduled reconciliation for that runtime's config. |
| `flee_to_unknown_rooms` | Base world; instances inherit it. | `spawns.actions.combat._flee_route_context` reads `inherited_system_config`. Animation and state payloads now report that same policy. |
| `cross_race_cooldown` | Each base or instance world owns its value. | `spawns.services.WorldGate.preflight` applies the local config's delay, in minutes, when the last logged-out character belonged to a different core faction. |

All three now appear explicitly in their applicable World Config YAML and
full exports, including defaults. Partial edits preserve omitted values.
Family imports preserve local overrides for reload and login policy; the flee
rule is neither copied into new instance configs nor accepted as an instance
override. This follows the existing runtime ownership rather than changing
which rules govern an instance.

A read-only check of Phalanx 23 and templates 27, 63, and 65 found
`never_reload: false`, `flee_to_unknown_rooms: true`, and
`cross_race_cooldown: 0` in every config. The omission therefore did not alter
that snapshot, but would have lost authored non-default values.

## Complete WorldConfig Inventory

“Base” below describes where the manifest stores the policy. “Local” means
both base and instance world documents contain their own value. Instance-only
settings apply only to authored instance templates. Related catalogs and
typed references are resolved within the imported family.

| Stored fields | Transfer contract |
| --- | --- |
| `starting_room`, `death_room` | Local portable room references. |
| `death_currency`, `death_currency_penalty`, `death_mode` | Local death policy; currency references use the shared currency code. |
| `death_route` | Local legacy value is retained; current deterministic routing uses the related `death_routing` policy instead. |
| `death_routing_source` | Instance-only selection of local or base routing. |
| `exits_to` | Family header `world_config.exits_to` link, not a `kind: world` field. |
| `instance_single_player`, `instance_time_control`, `instance_goal` | Instance-only admission, clock, and completion policy. |
| `leaderboards` | Base-world authored leaderboard configuration. |
| `clan_registration_cost`, `clan_registration_currency` | Base-world shared economy policy. |
| `never_reload`, `cross_race_cooldown` | Local gameplay rules, now explicitly portable. |
| `flee_to_unknown_rooms` | Base-world combat rule, now explicitly portable and inherited. |
| `combat_resolution_interval`, `default_roam_chance` | Base-world combat/roaming rules. |
| `combat_system`, `equipment_system`, `stat_system` | Base manifest fields `combat`, `equipment`, and `stats`; always exported through the current schema normalizers, including effective defaults. |
| `ability_progression`, `player_creation` | Base-world progression and character-creation policy. |
| `starting_equipment`, `starting_level`, `leveling_curve`, `max_level` | Base-world initial equipment and progression settings. |
| `is_narrative` | Base-world rule; apply derives `allow_combat`. |
| `auto_equip`, `players_can_set_title`, `globals_enabled`, `decay_glory` | Retained base-world manifest fields. Their presence in the contract does not establish that every historical runtime behavior is still implemented. |
| `announce_duel_results` | Base-world announcement policy, inherited by instances. |
| `can_select_gender`, `default_gender`, `non_ascii_names`, `name_exclusions` | Base-world character-creation policy. |
| `built_by`, `small_background`, `large_background` | Local presentation metadata. Backgrounds remain URL strings, not packaged image assets. |
| `pvp_mode` | Local canonical PvP rule. `allow_pvp` is a computed/import alias, not a separate database field. |
| `can_select_faction` | Derived from `player_creation.core_faction`; not independently exported. |
| `allow_combat` | Derived from `is_narrative`; not independently exported. |
| `is_classless` | Derived from `stats.class_profiles`; accepted as a legacy import field but not exported independently. |
| `can_create_chars` | Destination admission control. Not accepted or exported by world manifests. |
| `autoflee`, `has_corpse_decay` | Retained compatibility values without active gameplay consumers in this checkout. Not accepted or exported. |
| `death_routing_generation`, `death_routing_source_generation` | Local cache/routing version identities. Rebuilt by the destination; never imported. |

Model identity and timestamps (`id`, `created_ts`, `modified_ts`) are local
metadata. The related death-routing policy and ordered routes travel through
`death_routing`; compiled snapshots and reference-retention records are
destination implementation details.

`can_create_chars` is checked by character creation and exposed in the lobby.
It is an operational closure switch, like world maintenance, rather than a
character's authored starting policy. Import deliberately leaves the
destination value unchanged. The current `autoflee` references are legacy
payload serialization; `has_corpse_decay` is likewise serialized but has no
current corpse-processing consumer. Preserving these bytes would imply
gameplay guarantees the current implementation does not provide. If either
rule is revived, define its runtime ownership and manifest contract together.

## Surrounding World Metadata

- Name, descriptions, MOTD, visibility (`is_public`), and authored
  `initial_state` already travel in the world document.
- Base-world multiplayer mode is chosen when creating the destination.
  Instance declarations carry their family identity; numeric world IDs need
  not match. The importer does not change the base world's mode.
- Authorship, builder membership, server URLs, hosting/tier configuration,
  `auto_start`, `no_start`, and maintenance switches belong to the destination.
- Runtime lifecycle, timestamps, active participants, live state, and economy
  revision counters do not travel with authored manifests.
- The full map is a rebuilt cache. The room allocator is maintained by room
  import. `World.facts` is not the authored state contract; current authored
  seeds use `initial_state`, and live facts use scoped state storage.

## System Resets On Reimport

The system-map follow-up is complete. Base-world exports now always include
normalized `stats`, `combat`, and `equipment`. Empty stored maps export their
effective defaults, so reimport replaces previously customized destination
systems. Equipment settings containing only armor suggestions also survive.
The Config API uses these same normalized values for both its structured
payload and YAML. Instances continue to inherit the base systems.

Partial edits preserve omitted maps; explicit `{}` or `null` resets a map to
defaults. Resetting stats also derives classless status. Existing cross-system
and death-routing reference validation still applies. Supplied equipment can
replace invalid stored equipment without trying to normalize the old value
first. Invalid stored systems fail config/full export with a validation error,
including invalid armor references in stats, rather than being silently lost.

## Validation And Performance

Regression coverage uses default-created destinations with different IDs,
ordinary and family exports, repeated imports, resets to defaults, invalid
values, destination-policy preservation, and partial edits. Runtime checks
cover spawn reconciliation, unvisited-room flee restrictions in base and
instance worlds, and the cross-faction login delay.

System regression coverage adds custom-to-default ordinary and family round
trips, repeated imports, inherited runtime getters, partial omission and
explicit resets, equipment suggestions, invalid storage, repair, and atomic
rollback of failed batches.

Export and import add scalar fields to already loaded config objects. No
additional per-player or per-tick work is introduced. Flee payload resolution
uses the same inherited config already needed by world serialization; with
preloaded context/config relations the lookup performs zero queries.
System export normalizes already loaded configuration in the builder request;
it adds no runtime tick or player work. The Config API reuses its manifest's
normalized systems instead of normalizing them again for the JSON payload.
