from builders.models import Currency
from spawns.handlers import get_handler
from spawns.models import PlayerCurrencyBalance
from tests.base import WorldTestCase
from wr2_tests.utils import capture_game_messages, dispatch_text_command


class CurrencyCommandTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.obol = Currency.objects.create(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.world.default_currency = self.obol
        self.world.save(update_fields=["default_currency"])

    @staticmethod
    def _message_by_type(messages, message_type):
        for entry in messages:
            if entry["message"].get("type") == message_type:
                return entry["message"]
        return None

    def _dispatch(self):
        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "currencies")
        return self._message_by_type(messages, "cmd.currencies.success")

    def test_only_default_currency_is_shown_at_zero_without_gold(self):
        self.player.wallet_revision = 4
        self.player.save(update_fields=["wallet_revision"])

        message = self._dispatch()

        self.assertIsNotNone(message)
        self.assertEqual(
            message["data"],
            {
                "wallet_revision": 4,
                "balances": {"obol": 0},
            },
        )
        self.assertEqual(message["text"], "Currencies:\n  0 Obols")
        self.assertNotIn("gold", message["text"].lower())
        self.assertNotIn("medal", message["text"].lower())

    def test_default_is_first_and_zero_nondefaults_are_hidden(self):
        drachma = Currency.objects.create(
            world=self.world,
            code="drachma",
            name="Drachma",
            plural_name="Drachmas",
        )
        hidden = Currency.objects.create(
            world=self.world,
            code="laurel",
            name="Laurel",
            plural_name="Laurels",
        )
        PlayerCurrencyBalance.objects.bulk_create(
            [
                PlayerCurrencyBalance(
                    player=self.player,
                    currency=self.obol,
                    amount=1,
                ),
                PlayerCurrencyBalance(
                    player=self.player,
                    currency=drachma,
                    amount=3,
                ),
                PlayerCurrencyBalance(
                    player=self.player,
                    currency=hidden,
                    amount=0,
                ),
            ]
        )

        message = self._dispatch()

        self.assertEqual(
            list(message["data"]["balances"].items()),
            [("obol", 1), ("drachma", 3)],
        )
        self.assertEqual(
            message["text"],
            "Currencies:\n  1 Obol\n  3 Drachmas",
        )
        self.assertNotIn("Laurel", message["text"])

    def test_command_registers_help(self):
        help_data = get_handler("currencies").get_help_data(
            command_name="currencies"
        )

        self.assertEqual(help_data["format"], "currencies")
        self.assertEqual(
            help_data["description"],
            "Show your current currency balances.",
        )
