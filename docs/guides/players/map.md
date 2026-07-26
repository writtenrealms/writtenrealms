# Map

The game map records the rooms your character knows about. The current room,
rooms you have visited, and visible landmarks appear as room squares.

Lines between squares show directional exits. When a known room has an exit to
a room that is not on your map yet, the line stops at the edge of the unknown
space. This partial connection tells you that an unexplored exit is available
without revealing the destination room.

Entering the destination adds it to the known map, and the partial connection
becomes a full connection between the two room squares.
