import argparse
import json
import os
import sys
from pathlib import Path
from pprint import pprint

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR))

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

from spawns.state_payloads import (  # noqa: E402
    build_state_sync,
    get_player_with_related,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pretty print the state.sync payload for a player."
    )
    parser.add_argument("player_id", type=int, help="Database ID of the player.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of pprint.",
    )
    args = parser.parse_args()

    player = get_player_with_related(args.player_id)
    data = build_state_sync(player).model_dump()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        pprint(data)


if __name__ == "__main__":
    main()
