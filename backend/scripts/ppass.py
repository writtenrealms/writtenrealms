import os, sys

proj_path = os.environ.get('WR_PATH', "/code")
backend_path = os.path.join(proj_path, 'backend')
# This is so Django knows where to find stuff.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings.local")
sys.path.append(proj_path)

# This is so my local_settings.py gets loaded.
os.chdir(backend_path)

# This is so models get loaded.
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

# ==== script below ====

import argparse

from users.models import User

def main():
    parser = argparse.ArgumentParser(
        description="Change a player's password to 'p'. Don't use on prod!")
    parser.add_argument('user_id', nargs='?')
    args = parser.parse_args()

    user_id = args.user_id

    try:
        user = User.objects.get(pk=user_id)
    except (User.DoesNotExist, ValueError):
        try:
            user = User.objects.get(username__iexact=user_id)
        except User.DoesNotExist:
            try:
                user = User.objects.get(email__iexact=user_id)
            except User.DoesNotExist:
                print("User does not exist")
                return

    user.set_password('p')
    user.save()

    print("Password changed for %s (%s)" % (user.email, user.id))


main()
