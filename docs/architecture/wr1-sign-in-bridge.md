# Signing in to Core from Written Realms Alpha

## Scope and current implementation

Written Realms Alpha (WR1, `../Advent` + `../herald`) can act as a sign-in
provider for Written Realms Core (WR2, this repository). The applications retain
independent databases, normal login tokens, signing keys and account lifecycles.
Core starts with an empty database and creates accounts only as visitors arrive.
No characters, account privileges, subscriptions or game state are migrated.

WR1's provider and Core's receiver are implemented and disabled by default.
The opt-in [local test setup](../dev/wr1-bridge-local.md) runs both applications
with separate databases and two browser origins. No public Core deployment or
production activation is included. The public profile permission fix is also
implemented in both repositories.

The authoritative WR1 contract and operational switches are documented in
[Advent's bridge document](../../../Advent/docs/architecture/wr-core-sign-in.md)
and implemented in
[`users/core_sso.py`](../../../Advent/api/users/core_sso.py).
This is one fixed-client authorization-code bridge; it is not a full OIDC
provider and does not expose discovery or issue an ID-token JWT.

## Identity model

Core's `ExternalIdentity` table contains:

- `issuer`: the configured WR1 issuer, production `https://writtenrealms.com`.
- `subject`: the immutable WR1 user ID as a string.
- `user`: a foreign key to Core's own `User`.
- A database uniqueness constraint on `(issuer, subject)` and timestamps.

Issuer plus subject is the persistent identity pointer. Never interpret a WR1
user ID as a Core primary key, look up an existing link by email, or reuse the
existing `User.link_id` grouping field. Production, development and test issuers
must be distinct. WR1 must never reassign subjects under the same issuer.

Core owns its account attributes after creation. A subsequent WR1 email change
does not relink the identity or overwrite Core's email. Display names may be
suggested at signup; username collisions must not merge accounts. Roles,
moderation permissions, patron entitlements and consent remain Core-local.

The receiver creates the user and identity link in one transaction. An advisory
transaction lock per issuer/subject handles concurrent first visits before a
link exists. Migration `users.0022` adds database uniqueness for `Upper(email)`;
it refuses preexisting case-insensitive duplicates rather than merging them.
New bridge accounts use lowercase addresses; existing casing is preserved.
Provider-specific aliases such as Gmail dots or plus addressing are not merged.

## Implemented browser flow

```mermaid
sequenceDiagram
    actor Player
    participant Core
    participant Alpha as WR1 / Herald + Forge
    Player->>Core: /auth/wr1/start?world=<id>
    Core->>Core: Save browser-bound state, PKCE verifier and target
    Core-->>Player: Redirect to Alpha /auth/core/authorize
    Player->>Alpha: Existing WR1 JWT authorizes one-use code
    Alpha-->>Player: Redirect to Core /auth/wr1/callback?code=...&state=...
    Player->>Core: Submit callback to Core backend
    Core->>Core: Verify transaction and browser binding
    Core->>Alpha: Exchange code with client secret + PKCE verifier
    Alpha-->>Core: WR1 subject and email proof
    Core->>Core: Resolve account; request email proof or account switch if needed
    Player->>Core: Finish the browser-bound handoff
    Core->>Core: Create/reuse the account and identity link atomically
    Core-->>Player: Core credentials; open selected world
```

### Start route

The frontend route `/auth/wr1/start?world=<Core-world-id>` calls a same-origin
Core backend start endpoint. Featured cards in WR1 target this route. The
backend performs these steps:

1. Require a public root Core world and store its ID server-side. An arbitrary
   `next` URL is never accepted. The world is rechecked before token issuance.
2. Generate random `state` (32 random bytes, base64url without padding) and an
   RFC 7636 PKCE verifier (43–128 unreserved ASCII characters). Compute the
   challenge as unpadded base64url of SHA-256(verifier), using method `S256`.
3. Store a five-minute `WR1SignIn` transaction with hashed state, verifier,
   world, hashed browser binding, configured issuer/client/callback and status.
   Limit a browser to five pending attempts, in addition to IP throttling.
4. Bind it to the host-only `__Host-wr1-bridge` cookie: Secure, HttpOnly,
   SameSite=Lax, Path=/. Multiple pending transactions share a random browser
   binding but retain separate state, destinations and verifiers. Explicit
   local HTTP mode uses the separate non-Secure `wr1-bridge-local` cookie.
5. Redirect to WR1's frontend, **not directly to Forge**:
   `https://writtenrealms.com/auth/core/authorize`, with the parameters below.

Herald owns the WR1 JWT in local storage. Its route calls Forge with the JWT
header, returning through WR1 login if necessary. Core must never request or
receive that JWT.

### Callback route

The `/auth/wr1/callback` frontend passes the returned `code` and `state` to its backend;
the HttpOnly binding cookie accompanies that request automatically.

The backend validates state, expiry, browser binding, issuer/client/callback and
status, then atomically claims the pending transaction so duplicate callbacks
cannot race. No database locks are held during the HTTP exchange. The fixed
configured URL uses TLS verification, no redirects, 3-second connection and
5-second read timeouts, and a 16 KiB response limit.

The backend validates the returned `iss`, `aud`, subject and field types. The assertion comes
from the authenticated server exchange, not from browser-supplied identity
fields. It is plain JSON, not a token to feed to SimpleJWT. A consumed code whose
response is lost needs a fresh sign-in attempt; do not weaken replay protection
to make retries work.

After linking, the finish endpoint calls the existing
[`build_token_response`](../../backend/users/tokens.py) and return the usual
Core access/refresh tokens and user through a same-origin JSON response. It
checks active/invalid/temporary status under a Core user row lock before minting
tokens. The frontend uses `auth_set_tokens` / `user_set` and `router.replace`
to open `/worlds/<id>`. Callback/status responses carry no Core credentials;
this allows an explicit account-switch prompt before finishing.

The frontend promptly removes code/state from the address bar and retains only
the random state in session storage while verification is pending. Reloads use
the status endpoint. Handoff pages defer third-party scripts; responses use
no-store and no-referrer. Access/refresh tokens never enter a redirect URL.
Production ingress still needs callback query redaction in access logs, error
reporting and tracing. The destination remains in the backend transaction.

Implementation: [`users/wr1_sso.py`](../../backend/users/wr1_sso.py),
[`WR1SignIn.vue`](../../frontend/src/views/auth/WR1SignIn.vue),
[`users/models.py`](../../backend/users/models.py).

All five backend endpoints are POSTs under `/api/v1/auth/wr1/`: `start/`,
`callback/`, `status/`, `email/` and `finish/`. They require the configured Core
Origin and use the binding cookie, not an ambient JWT. Production must route
that prefix to Django on the same origin as the frontend. The local Vite proxy
already does this. Requests are throttled to 30/minute/IP; email requests also
have a 2/minute/IP limit and a 60-second per-attempt resend interval.

## Exact WR1 request/response contract

These are production URLs; use explicitly separate local values in development.

The query parameters sent to Herald are also its JSON request to
`POST https://writtenrealms.com/forge/api/v1/auth/core/authorize/`:

| Field | Value |
| --- | --- |
| `response_type` | `code` |
| `client_id` | Registered client, initially `wr-core` |
| `redirect_uri` | Exactly `https://core.writtenrealms.com/auth/wr1/callback` |
| `state` | 22–256 base64url characters; generate 32 random bytes |
| `code_challenge` | 43-character unpadded base64url SHA-256 digest |
| `code_challenge_method` | `S256` |

Herald receives a 201 response with `redirect_url` and `expires_in: 60`, then
navigates to that URL. Core receives only `code` and `state` in its callback.

Core redeems the code at
`POST https://writtenrealms.com/forge/api/v1/auth/core/exchange/`, with HTTP Basic
authentication (`client_id:client_secret`) and this JSON body:

```json
{
  "grant_type": "authorization_code",
  "code": "<43-character code from callback>",
  "redirect_uri": "https://core.writtenrealms.com/auth/wr1/callback",
  "code_verifier": "<verifier stored in this Core transaction>"
}
```

Successful response:

```json
{
  "iss": "https://writtenrealms.com",
  "aud": "wr-core",
  "sub": "12345",
  "email": "player@example.com",
  "email_verified": false,
  "name": null
}
```

Code expiry, reuse, incorrect PKCE/callback/issuer and disabled WR1 users return
400 `invalid_grant`. Incorrect client credentials return 401 `invalid_client`.
Malformed bodies have normal DRF 400 field errors. A disabled bridge returns
404 and throttled requests return 429. Show a recoverable error with a way to
restart or use Core's normal email login.

WR1 limits issuance to 20 requests/minute/user and exchange to 600/minute/source
IP. Codes are atomically consumed in PostgreSQL. Requests must not contain
characters, world data, user-selected account IDs or privilege flags.

## Account resolution and first-use proof

| Case | Core action |
| --- | --- |
| Existing `(issuer, subject)` link | Authenticate its Core user, subject to Core's own active/invalid checks. |
| New link, trusted verified email, no Core email collision | Create a normal Core account with unusable password and the link atomically. |
| New link, existing Core account with that email | Require proof of that Core account once, then create the link. Never silently merge by email. |
| New link, `email_verified` false | Complete email ownership proof before account creation/linking. |
| Browser already signed into a different Core account | Explicitly offer an account switch; do not attach the WR1 identity to the ambient session. |
| Temporary, inactive or invalid WR1 account | WR1 rejects authorization/exchange; do not bypass it with browser claims. |

For a first-use collision or unconfirmed Alpha email, Core sends a separate
eight-digit code to the asserted email. Its hash is stored on the browser-bound
transaction with the exact WR1 subject and world. It is not a normal Core login
token. Completion permits five incorrect guesses; resending never resets that
limit. The continuation expires 15 minutes after exchange. A native signup that
races the handoff also requires this proof before linking. Ambient Core login
never substitutes for proof or chooses the account to link. No WR1 code is reused.

WR1 uses its existing `User.email` and `User.is_confirmed` as the source of
truth: the assertion's `email_verified` is the current `is_confirmed` value.
By explicit owner decision, existing confirmations are trusted and the
transition assumes no past abuse of WR1's profile API. Core must accept this
evidence for a new account; confirmed WR1 players do not need another email
confirmation merely to cross over. WR1 adds no second verified-address field
and does not reset existing confirmations. Unconfirmed users still need email
verification, and linking an existing Core account still requires its account
proof as described above. Once linked, later handoffs use the stable identity
link without repeatedly asking for email verification.

Both applications' generic profile APIs now keep email and privileged account
flags read-only. Core's future email-change operations need explicit proof
rather than restoring writes through `UserSerializer`. This bridge deliberately
does not reuse `LoginLinkRequest`: its ordinary email-link flow does not yet
bind an immutable destination email or atomically consume links. Any future
reuse for account linking must first add those guarantees.

## Deployment and lifecycle

Deploy Core as its own release, using a fresh database and database role,
independent JWT/Django secrets, Redis/broker resources, persistent storage and
backups. Its frontend, Django, FastAPI, Celery workers and beat scheduler all
need explicit production settings. Verify capacity before sharing WR1's cluster.

The current [`config/settings/k8s.py`](../../backend/config/settings/k8s.py)
still hard-codes database name `wrealms` and site URL `writtenrealms.com`. Replace
those assumptions with Core-specific configuration, including working login
email delivery, host/origin settings, static assets and both WebSocket routes.
Serve the frontend/API/WebSockets under `core.writtenrealms.com` with TLS.

Core settings read these environment variables:

| Variable | Production value |
| --- | --- |
| `WR1_SSO_ENABLED` | `1` when ready; defaults to `0` |
| `WR1_SSO_ISSUER` | `https://writtenrealms.com` |
| `WR1_SSO_AUTHORIZE_URL` | `https://writtenrealms.com/auth/core/authorize` |
| `WR1_SSO_EXCHANGE_URL` | `https://writtenrealms.com/forge/api/v1/auth/core/exchange/` |
| `WR1_SSO_CLIENT_ID` | `wr-core` |
| `WR1_SSO_CLIENT_SECRET` | Same dedicated secret configured in WR1, at least 32 characters |
| `WR1_SSO_REDIRECT_URI` | `https://core.writtenrealms.com/auth/wr1/callback` |
| `WR1_SSO_ALLOW_INSECURE_LOCAL` | `0`; HTTP local mode is rejected unless DEBUG is true |

Keep the service secret in deployment secrets, never frontend `VITE_*`
variables or either application's JWT configuration. Trust only the configured
issuer; do not discover endpoints from callback input.

WR1's Helm `coreSSO` block can be enabled with a dedicated existing Secret after
Core is ready. Pilot the receiver with an empty `featuredWorlds` array, then
configure cards as `{id: "<Core-world-id>", name: "<title>"}`. WR1 never needs a
local World row for a Core destination. Switching WR1 SSO off removes the cards
and prevents new handoffs without invalidating existing Core sessions.

The two applications' logouts and bans are independent after Core issues its
own credentials. A WR1 ban prevents future handoffs but does not revoke an
existing Core token. Central revocation and cross-application logout are outside
this bridge; any future requirement must be explicit. Preserve native Core
email login so existing Core users can sign in during a WR1 outage or after WR1
is eventually retired.

All cross-application work occurs at sign-in: one bounded HTTP exchange and
indexed identity/email lookups. Gameplay does not query WR1. A beat task prunes
up to 10,000 expired attempts every ten minutes through an expiry index. State
and identity are uniquely indexed; browser hashes and case-insensitive email
lookups are indexed. Account-resolution locks cover one subject/account, not
all players. Regression tests check concurrent first visits and ensure repeat
sign-in query counts stay constant with another 1,000 accounts/identities.

## Remaining launch work

1. Complete the [local browser checklist](../dev/wr1-bridge-local.md), including
   character creation/game entry, account switching and ordinary email login.
2. Prepare Core's independent production release, database, email delivery and
   same-origin frontend/API routing, then configure DNS and TLS. Check callback
   log redaction and secure cookie behavior under real HTTPS.
3. Deploy Core's independent login and test world first; enable the provider
   pilot second; publish WR1 Featured Worlds cards last. Confirm gameplay and
   both WebSocket paths in the deployment before exposing the card broadly.

The older [passwordless-auth migration note](passwordless-auth-migration.md) is
superseded. WR1 does not need a passwordless migration or shared login-signing
secret for this bridge.
