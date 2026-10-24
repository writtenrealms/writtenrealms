# Items, Corpses, and Storage

## Using items and containers

Item commands accept common prepositions and optional leading articles such
as `the`, `a`, and `an`:

| Action | Examples |
| --- | --- |
| Examine an item or its contents | `l pouch`, `l in pouch`, `look inside the pouch`, `look at the spear`, `examine pouch` |
| Pick something up | `get spear`, `take spear`, `pick up the spear` |
| Take something from a container | `get spear from pouch`, `take spear out of the pouch` |
| Store something | `put spear in pouch`, `put spear into the pouch`, `put spear inside pouch` |
| Give something to an NPC | `give spear to guard` |
| Wear equipment | `put on the helmet`, `put the helmet on` |
| Remove equipment | `take off the helmet`, `take the helmet off` |

Short forms such as `get spear pouch`, `put spear pouch`, and `give spear guard`
still work. Use an explicit preposition for multiword names, for example
`get the shieldguard spear from the leather pouch` or
`give the shieldguard spear to the palace guard`. Item names and authored
keywords both identify items; every word in the selector must match.

Numbered and bulk selectors work with either phrasing: `get 2.spear from pouch`
takes the second matching spear, `put all.spear into pouch` stores every matching
carried spear, and `get all.spear from all.pouch` takes matching spears from every
accessible pouch. `loot from pouch` takes all its pickable contents. Containers
must be directly carried or in the room; another character's possessions are
not accessible. An unfinished command such as `get spear from` asks for the
missing container without moving anything.

### Bag capacity

Bags and other storage containers hold **100 items by default**, though builders
can give particular containers a different capacity. Each individual item counts,
even when identical copies appear together as a stack. A full bag refuses further
deposits; a bulk `put` that would exceed its capacity moves nothing.

You cannot put bags, other containers, or corpses inside a storage container.
A bag filled to its capacity can still be picked up and dropped. Death recovery
is separate: a corpse can retain your bags and their contents.

If a builder lowers a bag's capacity, its existing contents remain available.
Remove enough items before adding more. Any previously nested bags must be
removed before those bags accept new deposits.

## Retention and cleanup

Use `look corpse` or examine an item to see its retention notice. The time
shown is a snapshot taken when you inspect it. World builders can change all
supported durations; the following are the defaults.

- NPC corpses expire ten minutes after death. Emptying one shortens that to
  at most another minute.
- Player corpses retaining death equipment do not expire automatically.
  Once first emptied, they expire after **ten minutes**.
- Ordinary items and bags abandoned on the ground expire after one hour.
  Items you carry or wear are exempt.

An expiring container takes everything still inside with it. Refilling an
empty corpse, moving an abandoned bag, or examining an object does not extend
its deadline. Recovered items survive; picking up a bag also recovers its
contents. Dropping it again starts a fresh ground-item timer.

Taking a spawned item allows the world to replenish it. Returning it to its
original destination before replenishment prevents another copy appearing.
If a replacement already appeared, both copies remain; the world stops adding
more while enough are present. Items inside a chest you carry are not replenished.

An authored chest can remain in a room indefinitely while new items deposited
in it still expire. Only storage explicitly described as protected preserves
deposits. Valuables and quest items are not automatically protected from
cleanup; existing restrictions on moving quest items still apply.

Nearby players receive a short message when cleanup removes objects, such as
"The corpse of a wolf crumbles to dust." When multiple corpses decay together, the message
is "Several corpses crumble to dust." Ordinary item cleanup is described
separately. The live room display updates, while earlier console descriptions
remain a record of what you saw. Inspect again for a fresh time estimate.

Timers continue in empty rooms and while players are offline. A paused
instance pauses its timers; advancing moves them forward with gameplay.
World stop/reset or instance teardown may remove items regardless of their
timer, including unrecovered equipment.
