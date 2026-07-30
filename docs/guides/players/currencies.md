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

World interactions may award or charge a stated currency through a scripted
Trigger. After a successful 10-obol award, you see `You receive 10 obols.` and
other players in your current room may see `Joe receives 10 obols.` A charge
similarly tells you `You part with 10 obols.` and may tell the room
`Joe parts with 10 obols.` Your updated wallet balance remains private. An
invisible or logged-out character does not produce either room message.

Several awards and charges in one interaction succeed or fail together. Your
balance at the start of that step must cover all of its charges; an award in
the same step cannot be used to make an otherwise unaffordable charge succeed.
If the complete charge is unaffordable, no money changes and no success
messages appear.

Your wallet follows your character into instances of the same world. Entering
or leaving an instance does not reapply starting money. An explicit character
reset replaces balances with that world's configured starting balances.

For testing or an administrative correction, an authorized world builder can
set one of your balances directly. You receive a private message identifying
the currency and new amount, and the normal wallet update follows. This changes
only the selected currency and does not reset any other character progress.

Wallet updates carry a revision number so the client can ignore older balance
snapshots that arrive out of order. If the display ever appears stale, a fresh
state sync or the `currencies` command supplies the current authoritative
balances.

Player-to-player transfers and currency exchange are not part of the initial
WR2 currency system.
