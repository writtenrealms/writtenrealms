"""Optional deployment-wide email admission policy, shared with the gateway."""
import os


def environment_allowlist():
    # None means public registration. An enabled, empty list denies everyone.
    if os.environ.get('WR_ACCESS_RESTRICTED', '0') != '1':
        return None
    return frozenset(email.strip().casefold() for email in
                     os.environ.get('WR_ALLOWED_EMAILS', '').split(',') if email.strip())


def email_is_allowed(email, allowlist):
    return allowlist is None or (
        isinstance(email, str) and email.strip().casefold() in allowlist)


def token_is_allowed(payload, allowlist):
    return payload.get('token_type') == 'access' and email_is_allowed(
        payload.get('email'), allowlist)
