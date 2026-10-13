"""Public messages for database-backed platform admission."""


def access_denial_detail(mode='invite_only'):
    if mode == 'closed':
        return {
            'code': 'site_closed',
            'detail': 'This site is currently closed. Please check back later.',
        }
    return {
        'code': 'invite_only',
        'detail': 'This site is currently invite-only. Please sign in with an invited '
                  'email address, or contact the administrator to request an invitation.',
    }
