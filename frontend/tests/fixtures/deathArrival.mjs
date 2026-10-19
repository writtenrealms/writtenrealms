export const deathArrival = () => {
  const room = {
    id: 24, key: 'room.10', name: 'The Underworld',
    description: 'A starless black vault hangs over a barren sweep of jagged basalt.',
    inventory: [], chars: [], details: [], actions: ['touch stone'],
    quest_callouts: [], north: 25,
  };
  const actor = { id: 3, key: 'player.3', name: 'Onyx', health: 1, energy: 1, stamina: 1 };
  const death = {
    type: 'affect.death', text: 'You have been slain.',
    data: { _event_id: 'death', room, actor },
  };
  const sync = {
    type: 'cmd.state.sync.success', text: null,
    data: {
      _event_id: 'death-sync', reason: 'death', room, actor,
      world: { id: 13, name: 'Phalanx', instance_ref: null },
      map: [room, { id: 21, key: 'room.7', name: 'A Familiar Street' }],
      instance_time_control: null, prepared_abilities: [], who_list: [],
    },
  };
  const recovery = {
    type: 'notification./echo', text: 'You slowly come back to your senses.', data: {},
  };
  return { room, actor, death, sync, recovery };
};
