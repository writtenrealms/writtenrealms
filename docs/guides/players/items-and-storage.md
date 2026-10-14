# Items, Corpses, and Storage

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
