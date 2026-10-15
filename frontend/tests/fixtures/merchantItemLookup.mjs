export const merchantLookupState = (is_mobile = false) => {
  const merchant = { type: 'room', id: 158, key: 'room.158', name: "Armorer's Stock" };
  const item = {
    key: 'merchant_stock_entry.7', name: 'a crested bronze helm', keywords: 'helm bronze',
    type: 'equippable', level: 20, quality: 'normal', equipment_type: 'head', armor_class: 'heavy',
    description: 'A plain bronze helm carries a sun-faded horsehair crest and a replacement cheek guard whose hinge does not quite match its mate.',
    attributes: { constitution: 3, strength: 1 }, armor: 168, health_max: 0,
    value: { amount: 50, currency: 'obol' }, actions: [],
  };
  const message = {
    type: 'cmd.list.success', data: {
      merchant, stock: [{
        key: item.key, number: 1, unlimited: true, item,
        price: { amount: 75, currency: 'obol' },
      }],
      balance: { amount: 100, currency: 'obol' },
    },
  };
  return {
    is_mobile, lookup: null, last_message: { [message.type]: message },
    room: { key: merchant.key, merchant_provider: merchant, chars: [], inventory: [] },
    world: {
      allow_combat: true,
      economy: { default_currency: 'obol', currencies: { obol: { name: 'Obol', plural_name: 'Obols' } } },
      labels: { attributes: { constitution: 'Constitution', strength: 'Strength' } },
      equipment: {
        armor_classes: [{ key: 'heavy', label: 'Heavy' }],
        armor_proficiencies: { default: ['heavy'] },
      },
    },
    player: {
      key: 'player.1', level: 20, inventory: [],
      equipment: { head: {
        ...item, key: 'item.5', name: 'a battered helm',
        attributes: { constitution: 1, strength: 1 }, armor: 100,
      } },
    },
  };
};
