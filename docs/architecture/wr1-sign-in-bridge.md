# Signing in to Core from Written Realms Alpha

## Scope and current implementation

Written Realms Alpha (WR1, `../Advent` + `../herald`) can act as a sign-in
provider for Written Realms Core (WR2, this repository). The applications retain
independent databases, normal login tokens, signing keys and account lifecycles.
Core starts with an empty database and creates accounts only as visitors arrive.
No characters, account privileges, subscriptions or game state are migrated.

WR1's provider endpoints, Herald handoff page and optional Featured Worlds
cards are implemented, but disabled by default. **Core's receiving bridge is
not implemented yet.** The public profile permission fix is implemented in
both repositories. This document specifies the remaining Core work.

The authoritative WR1 contract and operational switches are documented in
[Advent's bridge document](../../../Advent/docs/architecture/wr-core-sign-in.md)
and implemented in
[`users/core_sso.py`](../../../Advent/api/users/core_sso.py).
This is one fixed-client authorization-code bridge; it is not a full OIDC
provider and does not expose discovery or issue an ID-token JWT.

## Identity model

Add an `ExternalIdentity` table in Core with:

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

Create the user and identity link in one transaction, with database constraints
handling concurrent first visits. Before launch, enforce one consistent email
normalization/uniqueness policy at the database boundary. The present
case-insensitive lookup plus case-sensitive unique field is insufficient under
concurrent registration. Do not collapse provider-specific aliases such as
Gmail dots or plus addressing.

## Browser flow to implement

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
    Core->>Core: Resolve or create account/link
    Core-->>Player: Core credentials; open selected world
```

### Start route

Implement the frontend route `/auth/wr1/start?world=<Core-world-id>` and a
same-origin Core backend start endpoint. Featured cards in WR1 already target
this route. The backend should:

1. Validate the world destination and store it server-side. Carry only a known
   Core world ID or an allowlisted relative route, never an arbitrary `next` URL.
2. Generate random `state` (32 random bytes, base64url without padding) and an
   RFC 7636 PKCE verifier (43–128 unreserved ASCII characters). Compute the
   challenge as unpadded base64url of SHA-256(verifier), using method `S256`.
3. Store a short-lived transaction with state, verifier, destination, creation
   time, browser binding and processing status. Five minutes is a reasonable
   initial transaction lifetime. Bound the number of outstanding transactions.
4. Bind it to a host-only cookie on Core: Secure, HttpOnly, SameSite=Lax,
   Path=/, preferably a `__Host-` name. Do not set Domain=.writtenrealms.com.
   Support multiple pending transactions per browser without swapping their
   destinations or verifiers. Local HTTP testing needs a separate cookie policy.
5. Redirect to WR1's frontend, **not directly to Forge**:
   `https://writtenrealms.com/auth/core/authorize`, with the parameters below.

Herald owns the WR1 JWT in local storage. Its route calls Forge with the JWT
header, returning through WR1 login if necessary. Core must never request or
receive that JWT.

### Callback route

Implement `/auth/wr1/callback` and a same-origin Core backend completion
endpoint. The frontend passes the returned `code` and `state` to its backend;
the HttpOnly binding cookie accompanies that request automatically.

Before contacting WR1, validate state, expiry, browser binding, intended issuer
and transaction status. Atomically claim the pending transaction so duplicate
callbacks cannot race. Do not hold database locks during the network request.
Exchange the code only with the fixed WR1 backend URL from configuration.
Use a bounded connection/read timeout, TLS verification, and no redirects.

Validate the returned `iss`, `aud`, subject and field types. The assertion comes
from the authenticated server exchange, not from browser-supplied identity
fields. It is plain JSON, not a token to feed to SimpleJWT. A consumed code whose
response is lost needs a fresh sign-in attempt; do not weaken replay protection
to make retries work.

After linking, call the existing
[`build_token_response`](../../backend/users/tokens.py) and return the usual
Core access/refresh tokens and user through a same-origin JSON response. Check
the Core user's active/invalid status explicitly before minting tokens. Reuse
the frontend's `auth_set_tokens` / `user_set` mutations and navigate to
`/worlds/:world_id/:slug?` using `router.replace`.

Remove callback code/state from the address bar promptly. Avoid analytics and
third-party content on the callback page; redact code/state from access logs,
error reporting and tracing. Never put normal access/refresh tokens into a
redirect URL. Preserve the destination through any required verification or
account-conflict step.

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

Core's normal email/Google login can establish account proof, but the pending
link must be bound to the exact WR1 subject, browser and verified destination
email. Resuming after an email link must not attach an unrelated WR1 identity.
Use a separate bounded continuation lifetime after exchange if email delivery
takes longer than the initial transaction. Never ask WR1 to reuse its code.

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
flags read-only. Core's future email-change and linking operations need explicit
proof rather than restoring writes through `UserSerializer`. Before reusing
Core's login-link machinery for linking, bind each request to its destination
email and make consumption atomic; the current `LoginLinkRequest` path needs
those guarantees. Check inactive users before token issuance as well.

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

Store WR1 issuer/frontend/API URLs, client ID, exact callback and bridge secret
in Core configuration. Keep the service secret in deployment secrets, never
frontend `VITE_*` variables or either application's JWT configuration. Trust
only the configured issuer; do not discover endpoints from callback input.

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
indexed identity/email lookups. Gameplay does not query WR1. Index transaction
expiry, prune abandoned transactions, avoid unbounded account scans, and test
concurrent signup/link attempts at the database boundary.

## Core implementation and acceptance checklist

1. Add the identity and browser-transaction models, constraints, configuration
   and backend start/completion endpoints.
2. Add frontend start/callback routes and normal auth-store integration;
   preserve the world destination across login, proof and conflict handling.
3. Implement proof-based first-use linking and consistent email uniqueness;
   keep ordinary Core email/Google login and refresh working.
4. Test tampered/expired/swapped state, missing browser binding, PKCE failure,
   arbitrary redirects, wrong issuer/audience, code replay, double callbacks,
   concurrent account creation, email collisions/changes, different ambient
   Core accounts, disabled users and upstream outages. Assert Core JWT subjects
   are Core IDs even when WR1 IDs differ.
5. Exercise the complete flow with two browser origins and separate databases.
   Verify access/refresh and both WebSocket paths after handoff, ordinary world
   permissions, character creation and direct email login afterward.
6. Deploy Core's independent login and test world first; enable the provider
   pilot second; publish Featured Worlds cards last. Update player guidance when
   the Core receiving feature is actually available.

The older [passwordless-auth migration note](passwordless-auth-migration.md) is
superseded. WR1 does not need a passwordless migration or shared login-signing
secret for this bridge.
