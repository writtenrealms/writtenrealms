# Room Description Writing Prompt

You are writing room descriptions for a text-based multiplayer world (MUD). Each room consists of a **title** and a **single descriptive paragraph**. Follow the style specification below exactly.

## Format

- **Title**: A short locational phrase, not a sentence. Prefer spatial/relational titles that orient the player within a larger structure: "Heart of the Temple of Seeds", "Northwest Corner of the Temple of Death", "Before the Basilica", "Outside the Temple of Bloom", "Stairs Between Two Temples". Titles should read like map labels, not chapter headings.
- **Body**: One paragraph. Never use headers, lists, or line breaks within a description. Match the sentence count to the room's importance:
  - 2 sentences for boring connector rooms, roads, paths, stairs, and transitional spaces whose main purpose is movement.
  - 3 sentences for normal rooms; this is the default for non-road rooms.
  - 4 sentences only for landmark, hub, dramatic, or mechanically/story-important rooms that warrant extra attention.
  Use roughly 25–120 words, scaling naturally with sentence count.

## Voice and tense

- Present tense, continuous state. Describe the room as it *always is*, never as an event in progress. Nothing may happen "just now" — a flame flickers eternally, water flows eternally, dust has gathered over time. The description must read identically on the hundredth visit.
- Impersonal camera. Avoid second person almost entirely — no "you see", "you enter", "you notice". (A rare "underfoot" or "beneath your feet" is acceptable, at most once every dozen rooms.)
- No named characters, no NPCs, no creatures present, no dialogue, no history lessons. The world's story is told entirely through physical evidence: wall carvings depicting scenes, offerings left on altars, stains on a pedestal, iron rings whose torches are long extinguished, stone worn smooth by countless footsteps.

## Sentence architecture

- Open with the room's dominant physical anchor — the thing a player would register first: "A golden altar stands at the center of the chamber…", "Towering shelves brimming with books line the walls…", "A faint path cuts across a dense, leafless forest…".
- Sentences are medium-length and layered: a concrete main clause followed by one or two participial or relative extensions that add texture. Example rhythm: "Two fluted columns stand at the entrance, their bases wrapped in vines whose green tendrils climb gracefully up the stone."
- Give inanimate things gentle agency with active verbs: vines *climb* and *creep*, shadows *dance*, branches *reach*, roots *snake* and *grip*, streams *trace* and *meander*, silhouettes *loom*. But keep the agency quiet — nothing dramatic ever occurs.
- Restrained adjectives. One or two per noun, always concrete and physical: *fluted* columns, *packed* earth, *skeletal* branches, *worn* steps. Avoid abstract or emotional adjectives (majestic, wondrous, terrifying) except sparingly at true landmark rooms.

## Navigation woven into prose

Every room must orient the player, but not every exit needs to be mentioned. Usually pick one primary direction to guide the player toward and describe it prominently. A second direction may be mentioned briefly when it helps continuity. Mention three or more directions only for sparse intersection rooms whose main purpose is routing. Compass exits are embedded naturally in the description, usually in the final sentence or two — never as a bare exit list:

- "To the north, a set of stairs descends toward the entrance of the Sanctuary, cut directly into the mountain's rocky slope."
- "The eastern archway leads to the heart of the temple."
- "The black stone hallway curves north and east."

Navigation must be origin-neutral. A room description must read correctly whether the player arrived from the north, south, east, west, above, below, or any other entrance. Avoid wording that assumes a previous route or authoring order, including "back toward", "returns to", "continues from", "ahead", "behind", "came from", or "entered from"; instead name the objective direction and destination.

Vary the constructions; do not use the same exit phrasing twice in a row. Where possible, the primary exit *previews* what lies beyond: a silhouette looming above, a glow radiating from a doorway, the murmur of water carrying from the next chamber, the back of a grand chair glimpsed through an archway.

## Continuity between adjacent rooms

Rooms are not islands. Physical features flow across room boundaries and must stay consistent:

- A stream entering from the west in one room exits eastward in the neighbor.
- A structure visible from one room may be seen from another angle in a neighboring room ("The roof of the Temple of Water snakes below, like a river winding across uneven terrain").
- A recurring ambient signature ties a region together — a faint scent of sulfur, volcanic ash on surfaces, the distant outline of the mountain — reappearing every few rooms in varied wording.
- Landmark objects (an altar, a tower, a fountain) are visible or audible from adjacent rooms before the player reaches them.
- Coordinate-adjacent rooms can matter even when they are not connected by an exit. Use a non-accessible neighbor as visible scenery, a boundary, or a blocked-direction justification when it is significantly relevant, such as the back wall of a building standing to the east. Do not imply travel is possible unless an exit exists.
- Directly adjacent rooms should not read like duplicates. When two rooms share architecture or materials, vary the opening image, first noun phrase, sentence rhythm, and navigation phrasing, especially in the first sentence; continuity should come from repeated physical facts, not repeated wording.

## Sensory layering

Each room leads with vision, then adds exactly one or two secondary senses — never all of them:

- **Sound**: the murmur of a stream, wax candles hissing, footsteps echoing on marble, talismans clinking in wind.
- **Smell**: sulfur, aged parchment, damp earth, blooming flowers mingling with old stone.
- **Touch/air**: a breeze drifting in, air grown cooler on descent, moisture seeping from walls, ground shifting slightly underfoot.

Establish visibility or light only in ways that remain true across visits: torchlight, a glow from an altar, a single candle, luminous stone, open sky, or oppressive darkness. Avoid direct dependence on time of day. Do not say the sun reflects on walls, casts a shadow, or bathes a room unless the phrasing is conditional, the room architecture makes it important, and this kind of conditional sunlight detail is used sparingly.

## Interior logic and symmetry

- Architecture is deliberate and geometric: symmetrical wings, colonnades in precise alignment, archways facing outward "in perfect symmetry". Describe buildings as intentional constructions.
- Let nature push back against that order as a recurring motif: roots lifting floor stones at odd angles, grass growing defiantly between slabs, vines refusing to be constrained, a mound of earth "indifferent to the temple's designs".
- Materials are specific and regionally consistent: basalt, pale stone, packed earth, dark wood, bronze, iron — pick a palette for a region and hold it.

## What to avoid

- No game mechanics, stats, or meta-language in descriptions.
- No "you" as protagonist, no addressing the reader.
- No assumed path of arrival or departure. Avoid "back", "ahead", "behind", "return", or similar language when it implies where the player came from or where they will go next.
- No events, arrivals, weather changes, or time-of-day dependence. Conditional light phrasing is allowed only when it truly fits the architecture and should be rare, such as "where the light of the heavens, when present, might bathe it".
- No lore exposition, proper-noun history, or explanation of purpose. Imply function through objects; never state "this room was used for rituals" — instead show the stained pedestal and the iron rings.
- No exclamation marks, no rhetorical questions, no similes stacked more than one per room.

## Reference examples (match this register exactly)

**Heart of the Temple of Seeds**
The floor is packed earth, uneven in places from the tread of countless footsteps. At the center stands a solid stone column, its surface intricately carved with curling vines and delicate leaves, with steep stairs spiraling upward toward the tower and downward into the cellar below. At its base rests an altar of dark stone, its edges engraved with patterns of seedlings unfurling. Surrounding the column, simple stone archways lead to the four wings of the temple, each facing outward in perfect symmetry.

**Northeast Alcove of the Temple of Beasts**
This secluded chamber bears the unmistakable signs of ritual sacrifice. A low stone pedestal occupies the center of the alcove, its surface stained dark from countless offerings made in ages past. Iron rings are embedded into the pedestal's sides, a grim reminder of its intended use. The alcove opens south to the rest of the temple.

**Stairs Between Two Temples**
The roof of the Temple of Water snakes below, like a river winding its way across uneven terrain. The steady murmur of a stream carries from that direction, while steep stairs cut into the mountainside extend up and down the volcano's slope. The outline of another temple looms above.

---

When given a room name, its exits, its neighbors, and any key features, produce a description in this exact style. If neighboring room descriptions are provided, maintain physical continuity with them.
