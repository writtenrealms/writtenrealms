import { DIRECTIONS } from "@/constants";

const KEY_CODE_TO_ARROW_KEY = {
  37: "ArrowLeft",
  38: "ArrowUp",
  39: "ArrowRight",
  40: "ArrowDown",
} as const;

type ArrowMovementEvent = Pick<KeyboardEvent, "key" | "which" | "shiftKey">;

export const getMovementDirectionFromArrowKey = (
  event: ArrowMovementEvent,
  shiftPressed = event.shiftKey,
) => {
  const key = event.key || KEY_CODE_TO_ARROW_KEY[event.which];
  if (!key) return null;

  if (shiftPressed) {
    if (key === "ArrowUp") return DIRECTIONS.up;
    if (key === "ArrowDown") return DIRECTIONS.down;
    return null;
  }

  if (key === "ArrowLeft") return DIRECTIONS.west;
  if (key === "ArrowUp") return DIRECTIONS.north;
  if (key === "ArrowRight") return DIRECTIONS.east;
  if (key === "ArrowDown") return DIRECTIONS.south;

  return null;
};
