import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

const [
  providerSource,
  configSource,
  roomConfigServiceSource,
  itemActionsSource,
  lookRoomSource,
  itemInfoSource,
  economySource,
  consoleSource,
  gameStoreSource,
  listSource,
  buybackSource,
] = await Promise.all([
  readSource("../src/core/merchantProviders.ts"),
  readSource("../src/views/builder/room/Config.vue"),
  readSource("../src/services/roomConfig.ts"),
  readSource("../src/core/itemActions.ts"),
  readSource("../src/components/game/console/LookRoom.vue"),
  readSource("../src/components/game/ItemInfo.vue"),
  readSource("../src/core/economy.ts"),
  readSource("../src/components/game/console/Console.vue"),
  readSource("../src/store/modules/game.ts"),
  readSource("../src/components/game/console/List.vue"),
  readSource("../src/components/game/console/OfferInventory.vue"),
]);

const compiledProviderSource = ts.transpileModule(providerSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const providers = await import(
  `data:text/javascript;base64,${Buffer.from(compiledProviderSource).toString("base64")}`
);
const compiledEconomySource = ts.transpileModule(economySource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const economy = await import(
  `data:text/javascript;base64,${Buffer.from(compiledEconomySource).toString("base64")}`
);

test("room merchant availability uses structured provider state only", () => {
  const provider = {
    type: "room",
    id: 42,
    key: "room.42",
    name: "The Lantern Market",
  };
  assert.deepEqual(
    providers.getRoomMerchantProvider({ merchant_provider: provider }),
    provider,
  );
  assert.equal(
    providers.getRoomMerchantProvider({ actions: ["shop"] }),
    null,
  );
});

test("merchant provider targets work for room and mob identities", () => {
  assert.equal(
    providers.merchantProviderTarget({ type: "room", key: "room.42", name: "Market" }),
    "room.42",
  );
  assert.equal(
    providers.merchantProviderTarget({ type: "mob", key: "mob.9", name: "Garron" }),
    "mob.9",
  );
  assert.equal(
    providers.merchantProviderTarget({ type: "room", name: "Market" }),
    "Market",
  );
  assert.equal(
    providers.merchantProviderIsAvailableInRoom(
      { type: "room", key: "room.42" },
      { merchant_provider: { type: "room", key: "room.42" } },
    ),
    true,
  );
  assert.equal(
    providers.merchantProviderIsAvailableInRoom(
      { type: "room", key: "room.42" },
      { merchant_provider: { type: "room", key: "room.99" } },
    ),
    false,
  );
  assert.equal(
    providers.merchantProviderIsAvailableInRoom(
      { type: "mob", key: "mob.9" },
      { chars: [{ key: "mob.9", is_merchant: true }] },
    ),
    true,
  );
});

test("direct room merchants replace SHOP with deduplicated LIST and OFFER actions", () => {
  assert.deepEqual(
    providers.roomActionsForMerchantProvider({
      merchant_provider: { type: "room", key: "room.42" },
      actions: ["craft", "shop", "LIST", "list", "OFFER", "offer"],
    }),
    ["craft", "LIST", "OFFER"],
  );
  assert.deepEqual(
    providers.roomActionsForMerchantProvider({
      actions: ["craft", "shop"],
    }),
    ["craft", "shop"],
  );
});

test("Room Config exposes a searchable, clearable room shop control", () => {
  assert.match(configSource, /ROOM SERVICES/);
  assert.match(configSource, /<h4>SHOP<\/h4>/);
  assert.match(configSource, /<ReferenceField/);
  assert.match(configSource, /:endpoint="merchantProfileEndpoint"/);
  assert.match(configSource, /@click="onClearMerchantProfile"/);
  assert.match(configSource, /@click="onSaveMerchantProfile"/);
  assert.match(configSource, /builder_merchant_profile_details/);
  assert.match(configSource, /world_id: route\.params\.world_id/);
  assert.doesNotMatch(configSource, /merchantProfileWorldId/);
  assert.match(configSource, /profileIsInherited/);
  assert.match(configSource, /Inherited from/);
  assert.match(configSource, /configCanEdit\.value/);
  assert.match(configSource, /This room is not assigned to you/);
});

test("Room Config saves only the selected profile id or null", () => {
  assert.match(
    roomConfigServiceSource,
    /\{ merchant_profile: merchantProfileId \}/,
  );
  assert.match(
    roomConfigServiceSource,
    /merchantProfileId: number \| null/,
  );
  assert.match(
    roomConfigServiceSource,
    /\/builder\/worlds\/\$\{worldId\}\/rooms\/\$\{roomId\}\/config\//,
  );
  assert.match(configSource, /merchantProfile\.value\?\.id \?\? null/);
});

test("direct room providers add LIST and OFFER ambient actions", () => {
  assert.match(lookRoomSource, /getRoomMerchantProvider\(room\.value\)/);
  assert.match(lookRoomSource, /roomActionsForMerchantProvider\(room\.value\)/);
  assert.match(lookRoomSource, /merchantProviderTarget\(roomMerchantProvider\.value\)/);
  assert.match(lookRoomSource, /`\$\{directMerchantAction\} \$\{merchantTarget\}`/);
  assert.doesNotMatch(lookRoomSource, /`shop \$\{merchantTarget\}`/);
});

test("direct room providers enable explicit room-backed selling", () => {
  assert.match(itemActionsSource, /Boolean\(roomMerchantProvider\)/);
  assert.match(itemActionsSource, /item\.sell_command = `sell \$\{item\.key\} to \$\{merchantTarget\}`/);
});

test("item hover and look values uppercase currency without changing normal money", () => {
  const catalog = {
    revision: 1,
    default_currency: "obol",
    currencies: {
      obol: { name: "Obol", plural_name: "Obols" },
    },
  };
  const value = { amount: 12, currency: "obol" };

  assert.equal(economy.formatMoneyUppercaseCurrency(value, catalog), "12 OBOLS");
  assert.equal(economy.formatMoney(value, catalog), "12 Obols");
  assert.equal(
    economy.formatMoneyUppercaseCurrency(
      { ...value, display: "Worth twelve Obols at the exchange" },
      catalog,
    ),
    "Worth twelve OBOLS at the exchange",
  );
  assert.equal(
    economy.formatMoneyUppercaseCurrency(
      { ...value, display: "An authored valuation" },
      catalog,
    ),
    "An authored valuation",
  );
  assert.equal(
    economy.formatMoneyUppercaseCurrency(
      { ...value, display: "An obolisk valuation" },
      catalog,
    ),
    "An obolisk valuation",
  );
  assert.match(itemInfoSource, /formatMoneyUppercaseCurrency\(props\.item\.value, world\.value\?\.economy\)/);
  assert.match(itemActionsSource, /actionData\.label = `\$\{verb\} \$\{formatMoneyUppercaseCurrency\(/);
  assert.match(itemActionsSource, /item\.sell_price/);
  assert.match(itemActionsSource, /"SELL FOR"/);
});

test("stock and buyback commands use generalized provider targets", () => {
  assert.match(listSource, /merchantProviderTarget\(merchant\.value\)/);
  assert.match(listSource, /buy_command: buyCommand\(entry\)/);
  assert.match(listSource, /`buy \$\{entry\.key\} from \$\{merchantTarget\}`/);
  assert.match(buybackSource, /merchantProviderTarget\(merchant\.value\)/);
  assert.match(buybackSource, /buyback_command: [^\n]*buybackCommand\(entry\)/);
  assert.match(buybackSource, /`buyback \$\{entry\.key\} from \$\{merchantTarget\}`/);
});

test("canonical LIST and OFFER responses use rich merchant views", () => {
  assert.match(consoleSource, /"cmd\.list\.success": List/);
  assert.match(consoleSource, /"cmd\.shop\.success": List/);
  assert.match(consoleSource, /"cmd\.offer\.success": OfferInventory/);
  assert.match(listSource, /buy # to purchase an item/);
  assert.match(listSource, /purchase-hint color-text-50 font-text-light ml-2/);
  assert.match(listSource, /Only the first/);
  assert.match(listSource, /isCurrentEntry\(entry\)/);
  assert.match(listSource, /isCurrentMerchantRoom/);
  assert.match(listSource, /merchantProviderIsAvailableInRoom/);
  assert.match(buybackSource, /Array\.isArray\(props\.message\?\.data\?\.offers\)/);
  assert.match(buybackSource, /`sell \$\{entry\.key\} to \$\{merchantTarget\}`/);
  assert.match(buybackSource, /\[action\]: true/);
  assert.match(buybackSource, /isCurrentEntry\(entry\)/);
  assert.match(buybackSource, /actionContext: isOffer \? 'inventory' : undefined/);
  assert.match(buybackSource, /isCurrentMerchantRoom/);
  assert.match(buybackSource, /merchantProviderIsAvailableInRoom/);
  assert.match(buybackSource, /Only the first/);
  assert.match(gameStoreSource, /MERCHANT_INVENTORY_ADD_MESSAGES/);
  assert.match(gameStoreSource, /MERCHANT_INVENTORY_REMOVE_MESSAGES/);
  assert.match(gameStoreSource, /player_inventory_changes_apply/);
  assert.match(gameStoreSource, /player_economy_snapshot_apply/);
});
