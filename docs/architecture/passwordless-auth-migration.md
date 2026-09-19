# Superseded: shared WR1/WR2 login-token proposal

The previous proposal to migrate WR1 to passwordless login and share JWT
signing secrets is superseded by the [Alpha → Core sign-in bridge](wr1-sign-in-bridge.md).

WR1 and Core keep separate databases, primary keys and normal login tokens.
Core accepts a one-use authorization code through an authenticated backend
exchange, then resolves a persistent `(issuer, WR1 user ID)` identity link and
issues its own credentials. Sharing JWT keys does not establish that mapping.

WR1 keeps its current password and Google login flows. Core keeps its existing
email and Google login flows. See the bridge plan for the exact protocol,
first-use account proof and remaining Core implementation work.
