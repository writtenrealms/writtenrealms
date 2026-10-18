# Sign-in and Access

Authentication proves who someone is. The platform access policy decides
whether that account may use your installation. Configure both before inviting
players. World visibility and builder permissions are additional controls.

## Local sign-in

Follow [Quick start](quick-start.md#first-administrator) to create the first
staff account. With `backend.config.settings.container`, the backend prints
email messages instead of sending them. Request a link from `/login` and open
the newest link in the backend logs.

The main site does not accept the password created by `createsuperuser`.
That password works at Django admin (`http://localhost:8000/admin/` locally,
`/admin/` behind your production proxy). A Django admin session does not replace
the main site's login-link flow.

## Production email

Core's mail adapter uses **Amazon SES in `us-east-1`**. It does not currently
read generic SMTP settings. Supply these variables to the Django API and any
worker processes that send email:

```dotenv
WR_SEND_EMAIL=1
WR_AWS_ACCESS_KEY=your-access-key
WR_AWS_SECRET_KEY=your-secret-key
SYSTEM_EMAIL_FROM=noreply@example.com
WR_SITE_BASE=https://realms.example.com
```

Use production Django settings, verify the sender identity in SES in that
region, and grant the sender credentials permission to send email. While SES
is in its sandbox, recipients must also be verified. Request
[SES production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html)
before sending login links to ordinary players.

The `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` names in the development Compose
template are not the `WR_AWS_*` names read by the mail adapter. Simply setting
them does not enable email. Production also disables printing unsent email, so
leaving `WR_SEND_EMAIL=0` will not provide login links in the logs.

Use a real administrator address to verify request, delivery, link hostname,
and successful sign-in. Treat login links as credentials. If email fails,
inspect the backend error and SES sender/recipient eligibility; an invitation
does not send mail by itself.

## Optional Google sign-in

Create your own web OAuth client following
[Google's setup guide](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid).
Configure its authorized JavaScript origins for the origins where your frontend
runs: for example, `http://localhost:5200` for local development and
`https://realms.example.com` for your hosted site.

Set the **same client ID** in both places:

| Setting | Where |
| --- | --- |
| `GOOGLE_CLIENT_ID` | Backend service environment |
| `VITE_GOOGLE_CLIENT_ID` | Frontend build environment |

For the local frontend, put `VITE_GOOGLE_CLIENT_ID` in
`frontend/.env.localBackend.local` and restart the frontend. Recreate the backend
after changing its Compose environment. For production, rebuild the frontend;
Vite variables are compiled into the browser bundle. Client IDs are public;
never put a client secret or server credential in a `VITE_*` variable.

A visible Google button is not an authentication test. Complete sign-in with
an allowed account and check that an uninvited account is rejected. Google
sign-in uses the same platform admission policy as email links. Independent
installations do not need the optional WR1 sign-in integration.

## Choose who can join

Open **Control Panel > Access** at `/staff` using a staff account.

| Mode | Admission |
| --- | --- |
| Open | Otherwise eligible accounts may join. |
| Invite only | Staff and accounts with a matching email in the whitelist. |
| Closed | Staff only. |

For a private server, save **Invite only**, then add each player's exact email.
Entries ignore email case and surrounding whitespace. Adding an entry makes
that address eligible; it does not create an account or send an invitation.
An existing account is not automatically whitelisted.

Use **Allow everyone to create worlds** to decide whether ordinary signed-up users may create
worlds. Turning it off preserves existing builder assignments and staff access.
Use world settings to control each world's visibility and admission.

Changes normally propagate within five seconds. **Maintenance mode** blocks
new non-staff world entry and world starts while current players continue.
**Closed** also blocks non-staff API access and commands; it does not stop
services or immediately disconnect every socket.

After configuring admission, check an invited account, an uninvited account,
staff access, and entry into the intended world. See
[Control Panel](control-panel.md) for monitoring, recovery actions, and audit
history. `SiteControl` and `AccessAllowlistEntry` in PostgreSQL are the source
of truth; legacy `WR_ACCESS_RESTRICTED` and `WR_ALLOWED_EMAILS` variables are ignored.
