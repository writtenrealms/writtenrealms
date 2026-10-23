export function characterItemsPage() {
  const item = (id, name, type = 'inert', extra = {}) => ({
    key: `item.${id}`, name, type, quality: 'common', level: 1,
    is_salvageable: false, inventory: [], ...extra,
  });
  return {
    view: 'private', id: 4, name: 'Fen', description: '', glory: 0,
    in_game: false, can_manage_items: true, can_transfer: false,
    world: { id: 12, name: 'Phalanx' },
    world_config: { craft_materials: [{ slug: 'bronze', name: 'Bronze' }] },
    character: {
      level: 20, archetype: 'mage', experience: 542455,
      equipment: { weapon: item(1, "a fisherman's trident", 'equippable', { is_salvageable: true }), head: null },
      inventory: [
        item(2, 'a Persian war spear', 'inert', { is_salvageable: true }),
        item(3, 'a pair of torn leather sandals', 'equippable', { is_salvageable: true }),
        item(4, 'an iron cage key', 'quest', { is_salvageable: true }),
        item(5, 'a worn ash spear', 'equippable'),
        item(6, 'a travel satchel', 'container', { is_salvageable: true,
          inventory: [item(7, 'a Persian felt cap', 'inert', { is_salvageable: true })] }),
        item(8, 'an empty linen pouch', 'container', { is_salvageable: true }),
      ],
      attributes: { constitution: 99, strength: 31, intelligence: 136 },
      stats: { armor: 700, weapon_damage: 63 },
      materials: { bronze: 1 },
    },
  };
}
