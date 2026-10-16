export function characterCreationNotice(world: any): string {
  if (world?.can_create_chars === false) return 'Character creation is disabled for this world.';
  const limit = world?.character_creation_limit;
  if (!limit || limit.used < limit.maximum) return '';
  const noun = limit.maximum === 1 ? 'character' : 'characters';
  return `This world allows ${limit.maximum} ${noun} per account. You have reached the limit.`;
}
