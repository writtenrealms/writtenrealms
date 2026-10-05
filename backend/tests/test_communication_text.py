from django.test import SimpleTestCase

from spawns.text_output import render_event_text


class TestCommunicationText(SimpleTestCase):
    def test_answers_use_the_viewers_perspective_without_showing_a_number(self):
        data = {"actor": {"name": "alden"}, "target": {"id": 2, "name": "mira"},
                "text": "Try the market.", "question_id": 3}
        self.assertEqual(render_event_text("cmd.answer.success", data),
                         "You answer 'Try the market.'")
        self.assertEqual(render_event_text("notification.cmd.answer.success", data, viewer_id=2),
                         "Alden answers you 'Try the market.'")
        self.assertEqual(render_event_text("notification.cmd.answer.success", data, viewer_id=3),
                         "Alden answers Mira 'Try the market.'")
        self.assertEqual(render_event_text("notification.cmd.answer.success", data),
                         "Alden answers Mira 'Try the market.'")

    def test_only_incoming_questions_show_a_spaced_question_number(self):
        data = {"actor": {"name": "ada"}, "text": "Where is the inn?", "question_id": 3}
        for command in ("ask", "answer", "chat", "gossip", "cchat"):
            verb = "clan chat" if command == "cchat" else command
            suffix = " [ 3 ]" if command == "ask" else ""
            with self.subTest(command=command):
                self.assertEqual(
                    render_event_text(f"cmd.{command}.success", data),
                    f"You {verb} 'Where is the inn?'",
                )
                self.assertEqual(
                    render_event_text(f"notification.cmd.{command}.success", data),
                    f"Ada {verb}s 'Where is the inn?'" + suffix,
                )

    def test_private_messages_name_recipient_only_in_sender_confirmation(self):
        data = {"actor": {"name": "ada"}, "target": {"name": "ben"}, "text": "Hello."}
        for command, outgoing, incoming in (
            ("tell", "tell Ben", "tells you"),
            ("whisper", "whisper to Ben", "whispers to you"),
        ):
            with self.subTest(command=command):
                self.assertEqual(render_event_text(f"cmd.{command}.success", data), f"You {outgoing} 'Hello.'")
                self.assertEqual(render_event_text(f"notification.cmd.{command}.success", data), f"Ada {incoming} 'Hello.'")

    def test_listening_status_and_toggles(self):
        help_text = (
            "\nAvailable channels: ask, chat, gossip."
            "\nUse listen <channel> [on|off] to join or leave."
        )
        for data, expected in (
            ({"channels": ["ask", "chat"]}, "You are currently listening to: ask, chat." + help_text),
            ({"channels": []}, "You are not currently listening to any channels." + help_text),
            ({"channel": "ask", "listening": True}, "You now listen to the ask channel."),
            ({"channel": "ask", "listening": False}, "You no longer listen to the ask channel."),
        ):
            with self.subTest(data=data):
                self.assertEqual(render_event_text("cmd.listen.success", data), expected)

    def test_message_body_stays_plain_text_and_invalid_ids_are_not_rendered(self):
        text = "<img src=x onerror=alert(1)> & 'quoted'"
        for question_id in (None, "3", -1, 0, True):
            with self.subTest(question_id=question_id):
                self.assertEqual(
                    render_event_text("notification.cmd.ask.success", {
                        "actor": {"name": "Ada"}, "text": text, "question_id": question_id,
                    }),
                    f"Ada asks '{text}'",
                )
