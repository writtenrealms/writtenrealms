import os, sys

# This is so Django knows where to find stuff.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings.local")
sys.path.append('/code/backend')


# This is so models get loaded.
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

# ==== script below ====

import argparse

def main():
    parser = argparse.ArgumentParser(description='')
    args = parser.parse_args()


main()