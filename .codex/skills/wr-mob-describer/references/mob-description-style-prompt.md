# Mob Description Writing Prompt

You are writing mobs (creatures and NPCs) for a text-based multiplayer world (MUD). Every mob has three components. Follow the specification below exactly.

## The three components

1. **Name** — the short, canonical reference used in combat and when the mob is mentioned. A minimal noun fragment, not a sentence.
2. **Room description** — one complete sentence shown when a player looks at the room the mob is in. It appears after the room's own description, so it must read as a brief line of ambient life within a scene, describing what any observer can see without needing to recognize the mob.
3. **Description** — the detailed portrait shown only when a player looks directly at the mob. One short paragraph.

## Name rules

- Common mobs: lowercase article + one evocative modifier + noun. "a burly stag", "a rabid dog", "an amiable hostess", "a shifty grifter". The game capitalizes it when needed, so never capitalize the article. Compound modifiers are welcome: "a fluffy-eared rabbit", "a large-horned ram", "a smooth-talking croupier".
- Exactly one modifier in most cases — pick the single most characterizing trait (physique, temperament, condition, or occupation quality): *gruff*, *stricken*, *jovial*, *lifeless*, *meticulous*. Avoid stacking adjectives.
- Unique-but-unnamed mobs take "the": "the mad mage", "the quartermaster", "the High Priest".
- Named individuals are capitalized proper names, optionally with title or epithet: "Taven Orset", "Father Anilas", "Harbinger Kadim", "Pyr, Archon of Ternium", "Kaarl the Shepherd".

## Room description rules

- One complete sentence, present tense, ending with a period. Roughly 6–15 words.
- **Screen width is at a premium.** Prefer the shortest natural sentence that conveys one distinctive action or posture. Avoid filler and optional trailing details; reserve richer detail for `description`. The word range is guidance, not a minimum to pad toward.
- The formula is *subject + one characteristic, repeatable action or posture*: "A city guard stands at attention." "A prowling tiger lurks hungrily." "A grumbling streetsweeper grudgingly grooms the road." The action must be ambient and loopable — something the mob could plausibly be doing every single time the player looks, never a one-time event.
- Add a short trailing detail only when it provides essential visual distinction; otherwise save it for `description`.
- **Prefer a visible description over the mob's proper name.** The room description is what everyone sees, including a stranger to the zone who has never heard of the mob or cannot identify them. Use supported appearance, species, clothing, or an obvious role as the subject. For a mob named Demeas whose source fields establish that he is a tall man practicing shieldwork, prefer "A tall man drills shieldwork beside a practice dummy." to "Demeas drills shieldwork beside a battered practice dummy." Do not invent appearance to avoid a name.
- A common mob's name can already be a suitable visible description, such as "a gray cat" or "a city guard". Recast names that imply knowledge unavailable at a glance: name "a novice ruffian" → room description "A shifty-looking lad glances over his shoulder." Keep hidden identities and unobservable affiliations out of the room description.
- "X is here, …" is an acceptable construction but vary it; most room descriptions should use an active verb instead.
- Second person is permitted and effective in small doses, framed as the mob's attention on the player: "A wild pig eyes you carefully." "An innkeeper smiles at you, hoping for your business."
- The sentence may anchor the mob to fixtures of its intended room (a jetty, a forge, a bar, a cage) — this coupling is desirable for stationary NPCs.

## Description rules

- One paragraph of 1–4 sentences. Scale length to importance: trivial critters and background villagers get 1–2 sentences; shopkeepers, quest NPCs, and bosses get 3–4. Never exceed 4.
- Present tense, habitual behavior. Like the room description, everything must read as ongoing state — twirling a dagger, muttering, stirring a pot — never a scene in progress.
- **Second person is allowed and encouraged here.** The mob may react to the player ("stares back at you, curious but unafraid"), and occasionally the player's own reaction may be voiced ("It looks so soft that you can barely resist the urge to run your hand through its fur"; "You're not entirely sure how close to it you should get"). Use this sparingly — at most one such beat per mob — and only when it adds character or tension.
- Open with the most striking concrete detail, often as a fronted phrase: "Thick of arms and thin of manners, he itches for a fight." "Rusty dagger held with two shaky hands and pointed right at your face, this is clearly one of this young lad's first attempts at a robbery." "Streaks of drool hanging off his snarling maw, this dog looks rabid, and hungry."
- **Imply, never narrate.** Backstory and personality come through physical evidence: scars "speak to a long history of brawling"; a clean-shaven face and pressed uniform show a guard "takes his profession seriously"; calloused hands on a young woman have "already seen many years of labor"; a helmet resting untouched on a desk tells you it's rarely worn. No exposition, no biography, no naming of feelings the body doesn't show.
- **Signal threat level.** For hostile or dangerous mobs, the description should let a player gauge what they're facing: "not your average helpless deer"; horns "sure to be quite painful should they find their way into your flesh"; a tiger "lean and gaunt, likely desperate for a good meal".
- **Wit is welcome for mundane mobs.** The everyday world carries a wry, affectionate humor: a cat that "cares not at all for your presence, or for the very concept of you to begin with" yet remains "quite convinced of its own ferocity"; a boy whose every bush hides "some villain or obstacle on his heroic journey"; scare quotes around a child's "sword" or "bow". Keep the humor observational, never mocking.
- **Pathos is earned through detail.** Tragic mobs get one precise, restrained emotional note: eyes "empty of any hope that you may show him some kindness"; sorrow "that only comes from an earnest belief in the goodness of the world dashed upon the wickedness and cruelty of its reality". One such line per mob, maximum.
- Clothing, props, and condition do the heavy lifting for NPCs: what they wear, how worn it is, what their hands are doing, what tool or vessel they hold. For animals: coat, eyes, movement pattern, and feeding behavior.
- Sentence fragments are permitted for alien or elemental beings, where terseness conveys otherness: "A fiery core encased in ice. A contradiction raging against the rules of reality itself."
- Use gendered pronouns freely for people and larger animals; "it" for small creatures and monsters.

## Consistency systems

- **Faction/corruption signatures**: mobs belonging to a common force share a consistent visual tell, woven into name, room description, or description as appropriate. (In the source world, corruption always manifests as a purple glow — in eyes, veins, wisps, sparks.) When writing a set of mobs for one faction or affliction, establish one such signature and repeat it with variation.
- **State variants**: an important mob may have multiple versions representing different states (enraged, focused, mourning). Keep the base description identical across variants and change only the sentence that expresses the current state, plus the room description.
- **Paired mobs**: mobs that share a scene should reference each other's activity (a journeyman "critiquing his apprentice's every move"; an apprentice glancing skittishly at his master). Write them together.

## What to avoid

- No game mechanics, stats, levels, or meta-language.
- No one-time events or narrative scenes; every behavior must be endlessly repeatable.
- No lore dumps, proper-noun history, or explanations of role beyond what is physically visible. (A single unobtrusive title or insignia reference is fine: a sash, a tabard, an embroidered symbol.)
- No stacked similes or purple prose; at most one figurative image per mob.
- No omniscient interiority — never state what a mob thinks or feels unless the body shows it or the phrasing is visibly inferential ("seems", "looks like", "as if").

## Reference examples (match this register exactly)

**a gray cat**
A gray cat sleeps here, curled up in a ball.
This cat cares not at all for your presence, or for the very concept of you to begin with. Rarely on the move except when looking for an even more comfortable spot to sleep on, this little beast is nevertheless quite convinced of its own ferocity.

**a novice ruffian**
A shifty-looking lad is here, constantly looking over his shoulder.
Rusty dagger held with two shaky hands and pointed right at your face, this is clearly one of this young lad's first attempts at a robbery. Sweat percolating on his forehead, he hesitates.

**a prowling tiger**
A prowling tiger lurks hungrily.
The massive feline doesn't blink as it skulks noiselessly through the shadows, striped fur rippling as the powerful muscles underneath draw taut. It looks lean and gaunt, likely desperate for a good meal.

**a shambling corpse**
A shambling corpse wanders blindly.
The half-rotted body walks without aim or purpose. Flesh as dry as papyrus stretches over the figure's face in a pained mockery of the features it had in life, still human enough to be deeply wrong. Faint violet wisps trail out of empty eye sockets like vaporous tears.

**Father Anilas**
A balding priest prays before a statue.
The sharp features and strong jawline under his remaining wisps of white hair were likely quite striking in his younger days. Crimson bands hang from the shoulders of his simple robes, indicating his elevated rank in the clergy. He looks up at the marble figure of Saint Lobelia as if seeking some guidance in her serene, unmoving expression.

**a frozen flame**
A frozen flame flies frantically to and fro.
A fiery core encased in ice. A contradiction raging against the rules of reality itself.

---

When given a mob concept (species or role, disposition, importance, and any faction or room context), produce all three components in this exact style, in this order: name on the first line, room description on the second, description as the final paragraph.
