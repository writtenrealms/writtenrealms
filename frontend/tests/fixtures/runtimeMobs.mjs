export const mob = {
  id: 123, key: 'mob.123', name: 'Menon', level: 1, type: 'humanoid', health: 100, health_max: 100, pending_deletion: false,
  room: { id: 70, name: 'Agora Northeast Stoa', relative_id: 56, world_id: 13, zone: 'Athens' },
  definition: { id: 35, name: 'Menon', slug: 'athens-menon', world_id: 12 },
  runtime: { id: 123, name: 'Menon', level: 1, description: 'A weathered philosopher turns a small pebble over in his palm.',
    room_description: 'Menon, a weathered philosopher, sits by a column with a pebble in his palm.', keywords: 'menon philosopher',
    health: 100, health_max: 100, stamina: 100, stamina_max: 100, energy: 100, energy_max: 100, attributes: { strength: 10, intelligence: 14 },
    talkable: true, attackable: false, aggression: 'none', fights_back: false, invisible: false, roaming_type: 'none', traits: [], loot: [], flags: {} },
  definition_record: { id: 35, slug: 'athens-menon', notes: 'A quiet place to test conversations.' },
  state: { data: {}, version: null },
  spawn: { placement: { entry_slug: 'menon', slot: 0 }, plan: { id: 17, name: 'Athens Stoa Philosopher', slug: 'athens-stoa-philosopher', run_id: 17 } },
  services: {}, factions: [], effects: [], factions_truncated: false, effects_truncated: false,
  conversation: { configured: true, world_enabled: true, supported: true,
    greeting: 'A seller calls his price fair; his buyer calls it theft. What makes either of them right?',
    knowledge: 'You once cut stone. You enjoy questions about fairness, courage, and friendship. Ask short, practical questions rather than giving lectures.', when: {} },
  inventory: { items: [], next_after: null, after: 0 }, equipment: { items: [], slots: {}, truncated: false },
};
mob.definition_record.conversation = { greeting: mob.conversation.greeting, knowledge: mob.conversation.knowledge, when: mob.conversation.when };
export const runtime = { id: 13, name: 'Phalanx', lifecycle: 'running', root_world_id: 12, inspected_at: '2026-10-26T23:00:00Z' };
export function preview(speech) {
  const packet = { common_local_knowledge: 'The Agora is a place for trade and conversation.', speaker: { name: 'Menon', approved_knowledge: mob.conversation.knowledge }, here: { name: mob.room.name }, listener: 'An unfamiliar visitor.', allowed_game_actions: [] };
  const instructions = 'You are a living person in this fictional game. Speak directly to the stranger. Use only the supplied local knowledge.';
  return { kind: 'preview', submitted: false, stored: false, generated_at: runtime.inspected_at, provider: 'openai', model: 'example-model',
    context: 'new_conversation', world_enabled: true, integration_enabled: true, pending_reply: false, unavailable_reason: null,
    instructions, knowledge_packet: packet,
    provider_body: { model: 'example-model', store: false, service_tier: 'default', input: [{ role: 'system', content: instructions }, { role: 'user', content: JSON.stringify(packet) }, { role: 'assistant', content: mob.conversation.greeting }, { role: 'user', content: speech }], max_output_tokens: 256, text: { format: { type: 'json_schema', name: 'utterance', strict: true, schema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'], additionalProperties: false } } } },
  };
}
