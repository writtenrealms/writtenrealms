# Passwordless Auth Migration Notes (WR1 Compatibility)

This document describes how to upgrade the Written Realms 1 auth system to be compatible with the WR2 passwordless auth flow implemented in this repo. The goal is that users can authenticate on either system with the same login-link + social workflow and tokens are interoperable.

## Target Behavior

- Passwordless login for end users (no password login, no password reset).
- Admin/staff can keep Django admin passwords.
- Email login links and Google/social logins both mint the same JWT access tokens.
- Access tokens are accepted with `Authorization: Bearer <token>` or `Authorization: JWT <token>`.
- Websocket layer (future FastAPI) should accept the same access token format.

## Token Format and Settings

- Use `djangorestframework-simplejwt` with HS256 and a shared secret.
- Access/refresh lifetimes should match WR2 (`ACCESS_TOKEN_LIFETIME`, `REFRESH_TOKEN_LIFETIME`).
- Ensure WR1 and WR2 share the same signing secret if you need cross‑system token acceptance.
- In Django settings:
  - `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES = ['rest_framework_simplejwt.authentication.JWTAuthentication', 'rest_framework.authentication.SessionAuthentication']`
  - `SIMPLE_JWT = {'SIGNING_KEY': <shared_secret>, 'ALGORITHM': 'HS256', 'ACCESS_TOKEN_LIFETIME': ..., 'REFRESH_TOKEN_LIFETIME': ..., 'AUTH_HEADER_TYPES': ('JWT', 'Bearer')}`

## Required API Endpoints (WR1)

### Email login link

- `POST /api/v1/auth/email/request/`
  - Body: `{ "email": "user@example.com" }`
  - Behavior: create user if missing, send one‑time login link, respond 201 with no tokens.
  - Throttle like `EmailThrottle`.
- `POST /api/v1/auth/email/confirm/`
  - Body: `{ "token": "<login_link_token>" }`
  - Behavior: validates token, marks it used, marks user confirmed, returns tokens + user.

### Google/social login

- `POST /api/v1/auth/google/login/`
  - Body: `{ "credential": "<google_id_token>" }`
  - Must enforce `email_verified` on the ID token.
  - Returns tokens + user data.

### Legacy compatibility

- Keep `POST /api/v1/auth/login/` but repoint it to email login request.
- Keep `POST /api/v1/auth/forgotpassword/` but repoint it to email login request.
- `POST /api/v1/auth/resetpassword/` should return HTTP 410 (disabled).
- `POST /api/v1/auth/refresh/` should use SimpleJWT refresh (`refresh` field).

## Data Model (WR1)

Add a login link table similar to:

```
LoginLinkRequest:
  user (FK)
  code_hash (sha256)
  used_ts (nullable)
  expires_ts (datetime, indexed)
  created_ts / modified_ts (from BaseModel)
```

Token flow:

- Generate `token = secrets.token_urlsafe(32)`.
- Store `sha256(token)` as `code_hash`.
- `expires_ts = now + LOGIN_LINK_TTL_SECONDS` (WR2 uses 15 minutes).
- Mark all existing unused login links as used before creating a new one.
- Always send the token via email; never store raw token.

## Email Content

- Login link format: `${SITE_BASE}/login-link/<token>`
- `SITE_BASE` should default to the frontend dev URL (WR2 uses `http://localhost:5173`) and be overrideable via env (WR2 uses `WR_SITE_BASE`).

## Frontend Expectations (WR1)

- Login form collects only email; submitting triggers `/auth/email/request/`.
- Signup form collects email/username/newsletter; submits to `/auth/signup/` and shows "check email".
- A `LoginLink` route should read `/login-link/:token`, call `/auth/email/confirm/`, store `access` + `refresh`, then redirect.
- Use `Authorization: Bearer <access>` header in API calls.

## Edge Cases / Security Notes

- Reject invalid user emails (`is_invalid` flag) on login confirm.
- Enforce `email_verified` for Google login.
- Throttle login link requests to reduce abuse.
- Expire and one‑time enforce login links.

## Known Differences from Legacy (WR1)

- No password reset flow for end users.
- Signup does not log a user in; it only sends the login link.
- Email confirmation becomes implicit (login link confirms email).

## Alignment Checklist

- [ ] SimpleJWT is installed and configured with shared secret.
- [ ] Email login request + confirm endpoints exist and match WR2 behavior.
- [ ] Login link storage uses hashed tokens + TTL.
- [ ] Email contents use `${SITE_BASE}/login-link/<token>`.
- [ ] Google login requires `email_verified`.
- [ ] Frontend uses Bearer auth and supports `/login-link/:token`.
