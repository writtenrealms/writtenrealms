# Onboarding persistence

`worlds.PlayerOnboarding` stores the template selected at character creation and
the time of a successful `leave` command. It belongs to the character, not the
runtime or quest attempt. Cleanup still moves offline characters into the base
runtime and deletes the old instance. The next `WorldGate.enter` reserves a fresh
private run when this record remains unfinished. A retained run resumes normally.
The entry task uses the gate's returned player/runtime for its connection payload.

Base-world entry restrictions run before reservation; the destination also passes
normal instance admission checks. Creation locks the template before the player,
matching the existing World -> Player order. Concurrent logins recheck the locked
player and share one reservation. Population and initial RP retain the existing
asynchronous login/state-sync sequence. There is no background respawn or extra
heartbeat work. Normal base-world logins add one indexed lookup by player ID;
retained instance logins add none. Only replacement creation takes the short
template lock. No lobby-wide per-character onboarding query is needed.

`OnboardingRewardGrant` has a unique `(onboarding, quest_slug, resolution)` key.
Quest resolution already locks the player; it claims this receipt and applies the
reward policy in the same transaction. Failure rolls both back. Receipts survive
runtime resets/deletion, quest replacement with the same slug, and leaving
onboarding. The whole authored reward package is once per outcome; ordinary step
effects still run. Author permanent gear in `rewards`, not temporary step effects.
Ordinary adventure rewards have no receipt. Reward logs use the existing 30-day
support retention; the small gameplay receipts last until character deletion.

Migration `worlds.0138_character_onboarding` only creates tables. It does not
guess whether older characters finished onboarding. Retrofitting a character
requires verified enrollment and reward history; do not infer completion from
their current world, an empty inventory, or a deleted run. Deleting the selected
template retains the unfinished record with a null template and blocks re-entry
with an unavailable-introduction error. Restore a valid template or explicitly
resolve that character's enrollment before expecting Play to succeed.
