# Currencies

Each WR2 world chooses its own currencies. Gold is not universal: one world may
use Obols, another Credits, and another several different currencies.

Use the `currencies` command to inspect your wallet:

```text
currencies
```

The world's default currency appears first, even when its balance is zero.
Other currencies appear when you hold a positive amount. Names and plurals are
authored by the world builder, so the command can show `1 Obol` and `2 Obols`
without assuming a particular kind of coin.

Prices and rewards identify both an amount and a currency. For example, an item
priced at 20 Obols is unrelated to a balance of 20 Guild Marks. WR2 never
silently exchanges one currency for another, and changing a world's default
does not convert anything already in your wallet.

Your wallet follows your character into instances of the same world. Entering
or leaving an instance does not reapply starting money. An explicit character
reset replaces balances with that world's configured starting balances.

Wallet updates carry a revision number so the client can ignore older balance
snapshots that arrive out of order. If the display ever appears stale, a fresh
state sync or the `currencies` command supplies the current authoritative
balances.

Player-to-player transfers and currency exchange are not part of the initial
WR2 currency system.
