export interface CommandHelp {
  command: string;
  commands?: string[];
  category?: string;
  name?: string;
  format: string;
  description: string;
  details?: string[];
  examples?: string[];
}

// These commands are handled by game/cmd before sending anything to Core.
// All game command topics come from the server's permission-filtered registry.
export const clientCommandHelp: CommandHelp[] = [
  {
    command: "quit",
    category: "Basic Commands",
    name: "Quit",
    format: "quit",
    description: "Disconnect this character from the game.",
    details: ["Type quit on its own in the command input."],
  },
  {
    command: "clear",
    category: "Basic Commands",
    name: "Clear Console",
    format: "clear",
    description: "Clear the messages displayed in your game console.",
    details: ["This does not clear your command history or change the world."],
  },
];

export function clientCommandHelpMessage(text: string) {
  const match = text.trim().match(/^(?:h|he|hel|help)\s+(quit|clear)$/i);
  if (!match) return null;
  const topic = clientCommandHelp.find((entry) => entry.command === match[1].toLowerCase());
  if (!topic) return null;
  return {
    type: "cmd.help.success",
    text: [topic.name, `Format: ${topic.format}`, topic.description, ...(topic.details || [])].join("\n"),
    data: { command: topic },
  };
}

export function commandHelpGroups(commands: CommandHelp[]) {
  const groups = new Map<string, string[]>();
  const seen = new Set<string>();
  for (const entry of [...clientCommandHelp, ...commands]) {
    const category = entry.category || "Commands";
    for (const command of entry.commands || [entry.command]) {
      if (seen.has(command)) continue;
      seen.add(command);
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category)!.push(command);
    }
  }
  return [...groups].map(([title, commands]) => ({ title, commands }));
}
