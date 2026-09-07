type InstanceFlag = "instance_single_player" | "instance_time_control";

// The convenience controls edit the canonical, two-space YAML emitted by the
// server. Other valid YAML remains editable in the main manifest editor.
export const instanceConfigFlags = (yaml: string) => {
  const read = (name: InstanceFlag): boolean | null => {
    const matches = [...yaml.matchAll(new RegExp(`^  ${name}: (true|false)[ \\t]*\\r?$`, "gm"))];
    return matches.length === 1 ? matches[0][1] === "true" : null;
  };
  const singlePlayer = read("instance_single_player");
  const timeControl = read("instance_time_control");
  return { singlePlayer, timeControl, editable: singlePlayer !== null && timeControl !== null };
};

export const setInstanceConfigFlag = (yaml: string, flag: InstanceFlag, enabled: boolean): string => {
  const flags = instanceConfigFlags(yaml);
  if (!flags.editable || (flag === "instance_time_control" && enabled && !flags.singlePlayer)) return yaml;
  const replace = (source: string, name: InstanceFlag, value: boolean) => source.replace(
    new RegExp(`^(  ${name}: )(true|false)([ \\t]*\\r?)$`, "m"), `$1${value}$3`,
  );
  let next = replace(yaml, flag, enabled);
  if (flag === "instance_single_player" && !enabled) next = replace(next, "instance_time_control", false);
  return next;
};
