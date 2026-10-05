from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from spawns.handlers import dispatch_command
from spawns.models import CommunicationMessage, Player
from tests.base import WorldTestCase
from tests.utils import (
    capture_game_messages,
    dispatch_text_command,
    dispatch_text_command_as_mob,
)


class ChatChannelTests(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

    def _online_player(self, name, *, world=None, room=None, channels="ask"):
        player = self.create_player(name, world=world, room=room)
        player.in_game = True
        player.channels = channels
        player.save(update_fields=["in_game", "channels"])
        return player

    def _dispatch(self, text, *, player=None):
        with capture_game_messages() as messages:
            dispatch_text_command((player or self.player).id, text)
        return messages

    @staticmethod
    def _message(messages, message_type, recipient):
        return next(
            (
                entry["message"]
                for entry in messages
                if entry["player_key"] == recipient.key
                and entry["message"].get("type") == message_type
            ),
            None,
        )

    def _success(self, messages, command, *, recipient=None):
        message = self._message(
            messages, f"cmd.{command}.success", recipient or self.player,
        )
        self.assertIsNotNone(message, messages)
        return message

    def _error(self, messages, command):
        message = self._message(messages, f"cmd.{command}.error", self.player)
        self.assertIsNotNone(message, messages)
        self.assertIsNone(
            self._message(messages, f"cmd.{command}.success", self.player),
        )
        return message

    def _subscriptions(self):
        self.player.refresh_from_db()
        return set(self.player.channels.split())

    def test_new_characters_listen_only_to_ask(self):
        self.assertEqual(self._subscriptions(), {"ask"})
        messages = self._dispatch("ask Where do I begin?")
        message = self._success(messages, "ask")
        self.assertEqual(message["data"]["question_id"], 1)
        self.assertEqual(message["data"]["text"], "Where do I begin?")
        self.assertEqual(message["data"]["actor"]["key"], self.player.key)

    def test_listen_lists_channels_without_changing_subscriptions(self):
        message = self._success(self._dispatch("listen"), "listen")
        for channel in ("ask", "chat", "gossip"):
            self.assertIn(channel, message["text"].lower())
        self.assertEqual(self._subscriptions(), {"ask"})
        self.assertFalse(CommunicationMessage.objects.exists())

    def test_listen_toggles_and_supports_idempotent_explicit_on_off(self):
        steps = (
            ("listen chat", {"ask", "chat"}),
            ("listen chat", {"ask"}),
            ("listen gossip on", {"ask", "gossip"}),
            ("listen gossip on", {"ask", "gossip"}),
            ("listen ask off", {"gossip"}),
            ("listen ask off", {"gossip"}),
            ("listen ask on", {"ask", "gossip"}),
        )
        for command, expected in steps:
            with self.subTest(command=command, expected=expected):
                self._success(self._dispatch(command), "listen")
                self.assertEqual(self._subscriptions(), expected)

    def test_invalid_listen_does_not_change_preferences(self):
        for command in ("listen missing", "listen ask maybe", "listen cc"):
            with self.subTest(command=command):
                self._error(self._dispatch(command), "listen")
                self.assertEqual(self._subscriptions(), {"ask"})

    def test_chat_gossip_and_answer_require_subscription_and_never_auto_join(self):
        for channel in ("chat", "gossip"):
            with self.subTest(channel=channel):
                self._error(self._dispatch(f"{channel} Hello."), channel)
        self._dispatch("listen ask off")
        self._error(self._dispatch("answer Over here."), "answer")
        self.assertEqual(self._subscriptions(), set())
        self.assertFalse(CommunicationMessage.objects.exists())

    def test_asking_without_listening_sends_to_listeners_without_joining(self):
        listener = self._online_player("Listener")
        nonlistener = self._online_player("Quiet", channels="")
        self._dispatch("listen ask off")
        messages = self._dispatch("ask Where do I begin?")
        question = self._success(messages, "ask")
        self.assertEqual(question["text"], "You ask 'Where do I begin?'")
        incoming = self._message(messages, "notification.cmd.ask.success", listener)
        self.assertIsNotNone(incoming)
        self.assertTrue(incoming["text"].endswith(" [ 1 ]"))
        for excluded in (self.player, nonlistener):
            self.assertIsNone(self._message(messages, "notification.cmd.ask.success", excluded))
        self.assertEqual(self._subscriptions(), set())
        self.assertEqual(CommunicationMessage.objects.get().text, "Where do I begin?")
        other_question = self._dispatch("ask Another question?", player=listener)
        self.assertIsNone(self._message(other_question, "notification.cmd.ask.success", self.player))

    def test_unsubscribed_asker_still_receives_only_answers_to_their_own_question(self):
        listener = self._online_player("Listener")
        other_asker = self._online_player("OtherAsker", channels="")
        self._dispatch("listen ask off")
        self._dispatch("ask First question?")
        self._dispatch("ask Second question?", player=other_asker)
        for command, expected, excluded in (
            ("answer Latest answer.", other_asker, self.player),
            ("answer 1 First answer.", self.player, other_asker),
        ):
            with self.subTest(command=command):
                messages = self._dispatch(command, player=listener)
                self._success(messages, "answer", recipient=listener)
                answer = self._message(messages, "notification.cmd.answer.success", expected)
                self.assertIsNotNone(answer)
                self.assertEqual(answer["data"]["target"]["id"], expected.pk)
                self.assertEqual(answer["data"]["target"]["name"], expected.name)
                self.assertIn("answers you '", answer["text"])
                self.assertNotIn("[", answer["text"])
                self.assertIsNone(self._message(messages, "notification.cmd.answer.success", excluded))
        self.assertEqual(self._subscriptions(), set())

    def test_answers_to_unsubscribed_askers_respect_presence_world_and_personal_mutes(self):
        listener = self._online_player("Listener")
        self._dispatch("listen ask off")
        self._dispatch("ask Can you help?")
        parallel_world = self.world.create_spawn_world()
        for changes in (
            {"in_game": False},
            {"world_id": parallel_world.id},
            {"mute_list": listener.name},
        ):
            with self.subTest(changes=changes):
                Player.objects.filter(pk=self.player.pk).update(**changes)
                messages = self._dispatch("answer 1 I can help.", player=listener)
                self._success(messages, "answer", recipient=listener)
                self.assertIsNone(self._message(messages, "notification.cmd.answer.success", self.player))
                Player.objects.filter(pk=self.player.pk).update(
                    in_game=True, world_id=self.spawn_world.id, mute_list="",
                )

    def test_unsubscribed_ask_still_obeys_character_and_account_moderation(self):
        self._dispatch("listen ask off")
        for subject in (self.player, self.user):
            for field in ("is_muted", "nochat"):
                with self.subTest(subject=type(subject).__name__, field=field):
                    setattr(subject, field, True)
                    subject.save(update_fields=[field])
                    self._error(self._dispatch("ask Where?"), "ask")
                    self.assertFalse(CommunicationMessage.objects.exists())
                    setattr(subject, field, False)
                    subject.save(update_fields=[field])

    def test_chat_and_gossip_notify_only_online_subscribers_in_runtime_world(self):
        remote_room = self.create_imported_room(relative_id=2, x=1)
        parallel_world = self.world.create_spawn_world()
        for channel in ("chat", "gossip"):
            with self.subTest(channel=channel):
                self._dispatch(f"listen {channel} on")
                listener = self._online_player(
                    f"Listener{channel}", room=remote_room, channels=channel,
                )
                nonlistener = self._online_player(f"Quiet{channel}")
                offline = self.create_player(f"Offline{channel}")
                offline.channels = channel
                offline.save(update_fields=["channels"])
                outsider = self._online_player(
                    f"Outside{channel}", world=parallel_world, channels=channel,
                )
                messages = self._dispatch(f"{channel} Hello listeners.")
                actor = self._success(messages, channel)
                self.assertEqual(actor["data"]["text"], "Hello listeners.")
                self.assertEqual(actor["data"]["actor"]["key"], self.player.key)
                event_type = f"notification.cmd.{channel}.success"
                self.assertIsNotNone(self._message(messages, event_type, listener))
                for excluded in (self.player, nonlistener, offline, outsider):
                    self.assertIsNone(self._message(messages, event_type, excluded))

    def test_ask_and_answer_share_question_ids_and_the_ask_audience(self):
        listener = self._online_player("Listener")
        witness = self._online_player("Witness")
        nonlistener = self._online_player("Quiet", channels="chat gossip")
        ask_messages = self._dispatch("ask Where is the harbor?")
        question = self._success(ask_messages, "ask")
        question_id = question["data"]["question_id"]
        notification = self._message(
            ask_messages, "notification.cmd.ask.success", listener,
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification["data"]["question_id"], question_id)
        self.assertIn(f"[ {question_id} ]", notification["text"])
        self.assertNotIn("[", question["text"])
        self.assertIsNone(self._message(
            ask_messages, "notification.cmd.ask.success", nonlistener,
        ))

        answer_messages = self._dispatch("answer To the east.", player=listener)
        answer = self._success(answer_messages, "answer", recipient=listener)
        self.assertEqual(answer["data"]["question_id"], question_id)
        self.assertEqual(answer["data"]["text"], "To the east.")
        self.assertEqual(answer["data"]["actor"]["key"], listener.key)
        self.assertEqual(answer["text"], "You answer 'To the east.'")
        notification = self._message(
            answer_messages, "notification.cmd.answer.success", self.player,
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification["data"]["question_id"], question_id)
        self.assertEqual(notification["text"], "Listener answers you 'To the east.'")
        third_party = self._message(answer_messages, "notification.cmd.answer.success", witness)
        self.assertIsNotNone(third_party)
        self.assertEqual(third_party["text"], f"Listener answers {self.player.name} 'To the east.'")
        self.assertNotIn("[", notification["text"])
        self.assertNotIn("[", answer["text"])
        self.assertEqual(sum(
            entry["player_key"] == self.player.key
            and entry["message"].get("type") == "notification.cmd.answer.success"
            for entry in answer_messages
        ), 1)
        self.assertIsNone(self._message(
            answer_messages, "notification.cmd.answer.success", nonlistener,
        ))

    def test_answer_latest_or_explicit_question_keeps_the_correct_thread(self):
        self._dispatch("ask First question?")
        self._dispatch("ask Second question?")
        latest = self._success(self._dispatch("answer The latest answer."), "answer")
        explicit = self._success(
            self._dispatch("answer 1 The first answer."), "answer",
        )
        self.assertEqual(latest["data"]["question_id"], 2)
        self.assertEqual(latest["text"], "You answer 'The latest answer.'")
        self.assertEqual(explicit["data"]["question_id"], 1)
        self.assertEqual(explicit["data"]["text"], "The first answer.")
        questions = {
            row.question_number: row
            for row in CommunicationMessage.objects.filter(channel="ask")
        }
        self.assertEqual(set(questions), {1, 2})
        for row in CommunicationMessage.objects.filter(channel="answer"):
            question = questions[row.answer_to_question_number]
            self.assertEqual(row.in_reply_to_id, question.id)
            self.assertEqual(row.session, question.session)

    def test_latest_question_is_world_latest_even_when_joining_after_it(self):
        asker = self._online_player("Asker")
        self._dispatch("listen ask off")
        self._dispatch("ask A question while Joe is away?", player=asker)
        self._dispatch("listen ask on")
        answer = self._success(self._dispatch("answer I can help."), "answer")
        self.assertEqual(answer["data"]["question_id"], 1)

    def test_answer_separator_allows_a_message_starting_with_a_number(self):
        self._dispatch("ask What is six times seven?")
        answer = self._success(self._dispatch("answer -- 42 is the answer."), "answer")
        self.assertEqual(answer["data"]["question_id"], 1)
        self.assertEqual(answer["data"]["text"], "42 is the answer.")
        self.assertEqual(
            CommunicationMessage.objects.get(channel="answer").text,
            "42 is the answer.",
        )

    def test_answer_rejects_absent_unknown_and_expired_questions(self):
        self._error(self._dispatch("answer No question yet."), "answer")
        self._dispatch("ask A question?")
        self._error(self._dispatch("answer 999 Missing question."), "answer")
        CommunicationMessage.objects.filter(channel="ask").update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self._error(self._dispatch("answer 1 Too late."), "answer")
        self._error(self._dispatch("answer Too late for latest."), "answer")
        self.assertEqual(CommunicationMessage.objects.count(), 1)

    def test_structured_answer_rejects_invalid_numeric_question_ids(self):
        self._dispatch("ask A question?")
        invalid_ids = (
            ("zero", 0),
            ("negative", -1),
            ("overflow", 2**63),
            ("huge string", "9" * 1000),
            ("boolean true", True),
            ("boolean false", False),
        )
        for label, question_id in invalid_ids:
            with self.subTest(question_id=label):
                with capture_game_messages() as messages:
                    dispatch_command(
                        command_type="answer",
                        player_id=self.player.id,
                        payload={"question_id": question_id, "message": "An answer."},
                    )
                self._error(messages, "answer")
                self.assertEqual(CommunicationMessage.objects.count(), 1)

    def test_questions_and_answers_do_not_cross_parallel_runtime_worlds(self):
        parallel_world = self.world.create_spawn_world()
        outsider = self._online_player("Outsider", world=parallel_world)
        self._dispatch("ask Is anybody outside?", player=outsider)
        self._error(self._dispatch("answer No local question."), "answer")
        messages = self._dispatch("ask A local question?")
        self.assertEqual(self._success(messages, "ask")["data"]["question_id"], 1)
        self.assertIsNone(self._message(
            messages, "notification.cmd.ask.success", outsider,
        ))
        messages = self._dispatch("answer Local answer.")
        self.assertIsNone(self._message(
            messages, "notification.cmd.answer.success", outsider,
        ))

    def test_all_communications_require_nonempty_text_and_do_not_log_errors(self):
        self._dispatch("listen chat on")
        self._dispatch("listen gossip on")
        self._online_player("River")
        for text in (
            "say", "yell", "emote", "tell", "tell River", "whisper",
            "whisper River", "chat", "gossip", "ask", "answer", "answer 1",
        ):
            with self.subTest(text=text):
                self._error(self._dispatch(text), text.split()[0])
        self.assertFalse(CommunicationMessage.objects.exists())

    def test_successful_communications_record_once_independent_of_audience_size(self):
        self._online_player("River", channels="ask chat gossip")
        self._online_player("Witness", channels="ask chat gossip")
        self._dispatch("listen chat on")
        self._dispatch("listen gossip on")
        commands = (
            ("say hello", "say", "hello"),
            ("yell hello", "yell", "hello"),
            ("emote waves.", "emote", "waves."),
            ("tell River hello", "tell", "hello"),
            ("whisper River hello", "whisper", "hello"),
            ("chat hello", "chat", "hello"),
            ("gossip hello", "gossip", "hello"),
            ("ask Where?", "ask", "Where?"),
            ("answer 1 There.", "answer", "There."),
        )
        for count, (command, channel, text) in enumerate(commands, start=1):
            with self.subTest(command=command):
                self._success(self._dispatch(command), channel)
                self.assertEqual(CommunicationMessage.objects.count(), count)
                row = CommunicationMessage.objects.order_by("-id").first()
                self.assertEqual(row.channel, channel)
                self.assertEqual(row.text, text)
                self.assertEqual(row.world_id, self.spawn_world.id)

    def test_character_and_account_mutes_block_communications_and_logging(self):
        self._online_player("River")
        self._dispatch("listen chat on")
        self._dispatch("listen gossip on")
        self._dispatch("ask Existing question?")
        for muted_object in (self.player, self.user):
            muted_object.is_muted = True
            muted_object.save(update_fields=["is_muted"])
            for command in (
                "say hello", "yell hello", "emote waves.", "tell River hello",
                "whisper River hello", "chat hello", "gossip hello", "ask Where?",
                "answer There.",
            ):
                with self.subTest(mute=type(muted_object).__name__, command=command):
                    self._error(self._dispatch(command), command.split()[0])
                    self.assertEqual(CommunicationMessage.objects.count(), 1)
            muted_object.is_muted = False
            muted_object.save(update_fields=["is_muted"])

    def test_mob_speech_and_emotes_are_also_archived_once(self):
        mob = self.create_mob("Harbor Guard")
        for count, command in enumerate(("say Halt.", "yell Halt.", "emote salutes."), start=1):
            with self.subTest(command=command):
                with capture_game_messages() as messages:
                    dispatch_text_command_as_mob(mob.id, command)
                self.assertIsNotNone(self._message(
                    messages, f"notification.cmd.{command.split()[0]}.success", self.player,
                ))
                self.assertEqual(CommunicationMessage.objects.count(), count)

    def test_archive_records_the_same_truncated_message_delivered_to_players(self):
        self._online_player("River")
        self._dispatch("listen chat on")
        text = "x" * 600
        for command, limit in (("say", 280), ("tell River", 280), ("chat", 560), ("emote", 560)):
            with self.subTest(command=command):
                message = self._success(self._dispatch(f"{command} {text}"), command.split()[0])
                self.assertEqual(message["data"]["text"], text[:limit])
                self.assertEqual(
                    CommunicationMessage.objects.order_by("-id").first().text,
                    message["data"]["text"],
                )

    def test_character_and_account_nochat_block_public_communications(self):
        self._dispatch("listen chat on")
        self._dispatch("listen gossip on")
        self._dispatch("ask Existing question?")
        for restricted_object in (self.player, self.user):
            restricted_object.nochat = True
            restricted_object.save(update_fields=["nochat"])
            for command in ("chat hello", "gossip hello", "ask Where?", "answer There."):
                with self.subTest(restriction=type(restricted_object).__name__, command=command):
                    self._error(self._dispatch(command), command.split()[0])
                    self.assertEqual(CommunicationMessage.objects.count(), 1)
            restricted_object.nochat = False
            restricted_object.save(update_fields=["nochat"])

    def test_tell_reaches_named_online_player_elsewhere_without_notifying_witnesses(self):
        remote_room = self.create_imported_room(relative_id=2, x=1)
        target = self._online_player("River", room=remote_room)
        witness = self._online_player("Witness")
        messages = self._dispatch("tell rIvEr A private message.")
        self._success(messages, "tell")
        target_message = self._message(messages, "notification.cmd.tell.success", target)
        self.assertIsNotNone(target_message)
        self.assertEqual(target_message["data"]["text"], "A private message.")
        self.assertIsNone(self._message(messages, "notification.cmd.tell.success", witness))

    def test_private_messages_reject_missing_offline_and_other_runtime_targets(self):
        self.create_player("Offline")
        outsider = self._online_player("Outsider", world=self.world.create_spawn_world())
        for command in (
            "tell Missing hello", "tell Offline hello", f"tell {outsider.name} hello",
            "whisper Missing hello", "whisper Offline hello",
            f"whisper {outsider.name} hello",
        ):
            with self.subTest(command=command):
                self._error(self._dispatch(command), command.split()[0])
        self.assertFalse(CommunicationMessage.objects.exists())

    def test_whisper_requires_the_target_to_be_in_the_same_room(self):
        target = self._online_player("River")
        witness = self._online_player("Witness")
        messages = self._dispatch("whisper River A quiet message.")
        self._success(messages, "whisper")
        self.assertIsNotNone(self._message(
            messages, "notification.cmd.whisper.success", target,
        ))
        self.assertIsNone(self._message(
            messages, "notification.cmd.whisper.success", witness,
        ))
        target.room = self.create_imported_room(relative_id=2, x=1)
        target.save(update_fields=["room"])
        self._error(self._dispatch("whisper River Too far away."), "whisper")
        self.assertEqual(CommunicationMessage.objects.count(), 1)

    def test_personal_mute_rejects_tell_and_whisper_without_logging(self):
        target = self._online_player("River")
        target.mute_list = "ALICE JOE"
        target.save(update_fields=["mute_list"])
        for command in ("tell River hello", "whisper River hello"):
            messages = self._dispatch(command)
            self._error(messages, command.split()[0])
            self.assertIsNone(self._message(
                messages, f"notification.cmd.{command.split()[0]}.success", target,
            ))
        self.assertFalse(CommunicationMessage.objects.exists())

    def test_muted_characters_can_still_tell_builders(self):
        builder = self._online_player("Builder")
        builder.is_builder = True
        builder.save(update_fields=["is_builder"])
        self.player.is_muted = True
        self.player.save(update_fields=["is_muted"])
        self.user.is_muted = True
        self.user.save(update_fields=["is_muted"])
        messages = self._dispatch("tell Builder Please review my restriction.")
        self._success(messages, "tell")
        self.assertIsNotNone(self._message(
            messages, "notification.cmd.tell.success", builder,
        ))
        self.assertEqual(CommunicationMessage.objects.count(), 1)

    def test_channel_query_count_does_not_grow_per_listener(self):
        listener = self._online_player("FirstListener")
        self._dispatch("ask Warm the command path?")
        with CaptureQueriesContext(connection) as small_queries:
            self._dispatch("ask One listener?")
        with CaptureQueriesContext(connection) as small_answer_queries:
            self._dispatch("answer One listener.", player=listener)
        Player.objects.bulk_create([
            Player(
                name=f"Listener{index}", user_id=self.user.pk,
                world_id=self.spawn_world.pk, room_id=self.room.pk,
                in_game=True, channels="ask",
            )
            for index in range(500)
        ])
        with CaptureQueriesContext(connection) as large_queries:
            messages = self._dispatch("ask Five hundred and one listeners?")
        notifications = [
            entry for entry in messages
            if entry["message"].get("type") == "notification.cmd.ask.success"
        ]
        self.assertEqual(len(notifications), 501)
        self.assertLessEqual(
            len(large_queries), len(small_queries) + 1,
            "Channel fan-out must not issue a query per listener.",
        )
        with CaptureQueriesContext(connection) as large_answer_queries:
            messages = self._dispatch("answer Five hundred and one listeners.", player=listener)
        self.assertEqual(sum(
            entry["message"].get("type") == "notification.cmd.answer.success"
            for entry in messages
        ), 501)
        self.assertEqual(sum(
            entry["message"].get("text") == "FirstListener answers you 'Five hundred and one listeners.'"
            for entry in messages
        ), 1)
        self.assertLessEqual(
            len(large_answer_queries), len(small_answer_queries) + 1,
            "Including the original asker must not issue a query per listener.",
        )
        for queries in (small_answer_queries, large_answer_queries):
            self.assertFalse(any(
                'SELECT "spawns_player"."world_id" AS "world_id"' in query["sql"]
                for query in queries
            ), "Answer events already know their runtime world.")
