export type EntryMessageMode = "all" | "reveal" | "replace";

export interface WorldEntryMessage {
  paragraphs: string[];
  mode: EntryMessageMode;
}

export interface WorldEntryState {
  worldId: number | string | null;
  message: WorldEntryMessage | null;
}

export const initialWorldEntryState = (): WorldEntryState => ({ worldId: null, message: null });

export const entryMessageParagraphs = (text: unknown): string[] => (
  typeof text === "string"
    ? text.replace(/\r\n?/g, "\n").split(/\n[\t ]*\n+/).map(part => part.trim()).filter(Boolean)
    : []
);

export const applyWorldEntry = (current: WorldEntryState, world: any): WorldEntryState => {
  if (world?.id == null || String(current.worldId) === String(world.id)) return current;
  // Pregame and partial payloads need not contain the entry configuration.
  // Wait for the destination's full state instead of borrowing the old message.
  if (!Object.prototype.hasOwnProperty.call(world, "entry_message")) return initialWorldEntryState();
  const paragraphs = entryMessageParagraphs(world.entry_message);
  const mode = world.entry_message_mode === "reveal" || world.entry_message_mode === "replace"
    ? world.entry_message_mode : "all";
  return { worldId: world.id, message: paragraphs.length ? { paragraphs, mode } : null };
};

export const startEntryMessage = (
  message: WorldEntryMessage,
  show: (paragraphs: string[]) => void,
): (() => void) => {
  const { paragraphs, mode } = message;
  if (mode === "all" || paragraphs.length <= 1) {
    show(paragraphs);
    return () => {};
  }
  let index = 0;
  show(paragraphs.slice(0, 1));
  let timer: ReturnType<typeof setTimeout>;
  const advance = () => {
    index += 1;
    show(mode === "replace" ? paragraphs.slice(index, index + 1) : paragraphs.slice(0, index + 1));
    if (index < paragraphs.length - 1) timer = setTimeout(advance, 3000);
  };
  timer = setTimeout(advance, 3000);
  return () => clearTimeout(timer);
};
