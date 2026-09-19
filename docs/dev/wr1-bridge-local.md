# Test Alpha-to-Core sign-in locally

This opt-in setup runs the real bridge using sibling `Advent`, `herald` and
`writtenrealms` checkouts and separate local PostgreSQL databases. It requires
Docker Compose and Core's normal `.env` from [environment setup](environment-setup.md).
No public DNS, production settings or production accounts are needed.

From `writtenrealms`:

```sh
./scripts/wr1-bridge-local up
./scripts/wr1-bridge-local smoke
./scripts/wr1-bridge-local status
```

`up` builds/starts both stacks, applies migrations and creates only reserved
test accounts and a small public **Alpha Bridge Test** world if absent. It
preserves existing local data, and never imports or replaces a database. Core's
new email uniqueness migration stops if the database contains case-insensitive
duplicate addresses; resolve those intentionally before retrying. The helper
refuses non-local Docker endpoints.

The generated bridge secret lives in ignored `.env.wr1-bridge.local` with mode
0600. Keep it local. The two Compose overlays enable the bridge only for this
setup. Repeating `up` reuses the same secret, fixtures, volumes and world ID;
linked test accounts remain linked.

## Addresses and fixture accounts

| Application | Browser address |
| --- | --- |
| Alpha / Herald | `http://localhost:5173` |
| Alpha API gateway | `http://localhost/forge/api/v1/` |
| Core | `http://localhost:5200` |
| Core API | `http://localhost:8000/api/v1/` |

Use **localhost** consistently; `127.0.0.1` is a different browser origin.
Port 80 is Alpha's API/WebSocket gateway, not its frontend. Stop Minikube first
if it occupies port 80. The launcher starts Herald on 5173 and Core's frontend
on 5200, so stop any separate dev servers using those ports.

Every Alpha fixture uses the password **`alpha-bridge-local`**:

| Alpha email | Initial purpose |
| --- | --- |
| `bridge-player@example.test` | Ordinary confirmed account; repeat visits |
| `bridge-first-visit@example.test` | Spare confirmed account for a fresh first visit |
| `bridge-collision@example.test` | Existing Core account with the same email; first-use proof |
| `bridge-unconfirmed@example.test` | Unconfirmed Alpha email; first-use proof |

`bridge-api-check@example.test` is reserved for `smoke`, which exercises the
real authorization/exchange, Core access and refresh credentials, repeat
identity, destination and replay rejection. It leaves the browser fixtures
untouched. No Alpha password is copied into Core.

## Browser checklist

1. Open `http://localhost:5173/login` and log in as `bridge-player@example.test`.
   In the Alpha lobby, click **Alpha Bridge Test** under **Featured Worlds**.
   Expect the Core world lobby at port 5200, already authenticated. A confirmed
   first visit needs no extra email prompt. Profile name and character creation
   remain ordinary Core choices; no Alpha character is imported.
2. Reload Core, then return to Alpha and use the card again. Expect the same
   Core account and world, with no additional verification. Try ordinary world
   entry/character creation to exercise the normal authenticated game flow.
3. Sign out of Alpha and open the Core start link printed by `status`. Expect
   Alpha login to resume the handoff after authentication.
4. With the player account still signed into Core, log into Alpha as the fresh
   `bridge-first-visit@example.test` fixture and open the card. Core must ask
   before switching the browser to the other account.
5. Test `bridge-collision@example.test` and `bridge-unconfirmed@example.test`.
   Each needs an email code on its first handoff. Click **EMAIL ME A CODE** and
   read the eight-digit code from Core's local backend logs:

   ```sh
   docker compose logs --tail=100 backend
   ```

   Email delivery is disabled in this local setup. Enter the code in the same
   tab. The collision case reuses its existing Core account; the unconfirmed
   case creates an account only after proof. Both retain the selected world.
   Subsequent handoffs use the identity link without more email proof.
6. Try an incorrect code, reloading while awaiting proof, and using a callback
   URL in a different browser profile. A wrong code must not sign in; reload
   can resume verification in the same tab; another browser must reject the
   callback. There are five guesses per handoff and a 60-second resend delay.
7. Sign out of Core and use its ordinary email login with the linked address.
   Open the login link printed in backend logs. The same Core account should
   be accessible independently of Alpha.

The initial handoff expires after five minutes; the email/switch continuation
expires 15 minutes after exchange. Start again from Alpha if either expires.
Avoid rapid repeated restarts: a browser may have five pending attempts, and
endpoints also have IP throttles. Previously consumed callbacks cannot sign in
again. Never paste live callback URLs or credentials into issue reports.

## Lifecycle and implementation

```sh
./scripts/wr1-bridge-local stop
```

This stops both local stacks, including the helper's Herald container, without
deleting volumes. Run `up` to resume. To return to ordinary development without
the bridge, stop this setup and start each repository using its normal Compose
workflow without these overlays. Existing identity links remain local data.

The Core frontend proxies `/api/v1/auth/wr1/` to Django on its own browser
origin. Core reaches Alpha's API through a shared Docker network, using a
dedicated client/secret plus PKCE. The browser never receives that secret.
Local issuer, callbacks and the non-Secure cookie are distinct from production;
insecure HTTP is accepted only with the explicit local flag and DEBUG.

See the [architecture and production configuration](../architecture/wr1-sign-in-bridge.md)
for the contract and remaining deployment work. HTTPS cookie and ingress/log
behavior still need validation in the eventual Core deployment.

Focused checks (from the Core root unless noted):

```sh
docker compose exec -T backend python manage.py test tests.test_wr1_sso tests.test_profile_permissions tests.test_auth tests.test_users --settings=config.settings.testing --noinput
docker compose exec -T backend python manage.py makemigrations --check --dry-run
# From frontend/:
node --test tests/wr1SignIn.test.mjs
npm test
npm run build
# From the Core root:
make docs-build
./scripts/wr1-bridge-local smoke
```

Implementation verification: the backend command passed **38 tests**; the
frontend suite passed **191 tests**, including the three new bridge helper
tests. Migration consistency reported **No changes detected**. Frontend and
strict guide builds passed, and the real local smoke command passed. A browser
check also followed the Alpha Featured World card through an account-switch
prompt to the authenticated Core world. The remaining browser scenarios above
are the owner acceptance checklist.

The frontend build also needed a one-line correction to an existing unused
loop-index warning in `AbilityCastMessage.vue`; rendered parts and keys remain
the same. Builds retain existing Browserslist/chunk-size warnings, and the full
frontend suite logs preexisting Vite WebSocket-port warnings despite passing.
