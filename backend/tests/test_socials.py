from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from builders.models import AbilityDefinition, MobDefinition, Social, Trigger
from config import constants as adv_consts
from core.scoped_state import STATE_SCOPE_CHARACTER, replace_state_snapshot
from spawns.models import Mob
from spawns.socials import resolve_social_for_command
from tests.base import WorldTestCase
from worlds.models import Room, World, WorldConfig
from tests.utils import (
    capture_game_messages,
    dispatch_text_command,
    dispatch_text_command_as_mob,
)


class TestSocialCommands(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])

    def _social(self, cmd, *, world=None, priority=0, **overrides):
        values = {
            "world": world or self.world,
            "cmd": cmd,
            "priority": priority,
            "msg_targetless_self": f"You {cmd}.",
            "msg_targetless_other": f"{{{{ Actor }}}} {cmd}s.",
            "msg_targeted_self": f"You {cmd} at {{{{ target }}}}.",
            "msg_targeted_target": f"{{{{ Actor }}}} {cmd}s at you.",
            "msg_targeted_other": (
                f"{{{{ Actor }}}} {cmd}s at {{{{ target }}}}."
            ),
        }
        values.update(overrides)
        return Social.objects.create(**values)

    def _online_player(self, name, *, world=None, room=None):
        player = self.create_player(
            name,
            world=world or self.spawn_world,
            room=room or self.room,
        )
        player.in_game = True
        player.save(update_fields=["in_game"])
        return player

    def _entries(self, messages, message_type, *, recipient=None, social=None):
        entries = []
        for entry in messages:
            message = entry["message"]
            if message.get("type") != message_type:
                continue
            if recipient is not None and entry["player_key"] != recipient:
                continue
            if social is not None and message.get("data", {}).get("social") != social:
                continue
            entries.append(entry)
        return entries

    def _entry(self, messages, message_type, *, recipient=None, social=None):
        entries = self._entries(
            messages,
            message_type,
            recipient=recipient,
            social=social,
        )
        return entries[0] if entries else None

    def test_targetless_social_emits_actor_and_witness_events(self):
        self._social(
            "nod",
            msg_targetless_self="You nod in agreement.",
            msg_targetless_other="{{ Actor }} nods in agreement.",
        )
        witness = self._online_player("Witness")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod")

        actor_entry = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="nod",
        )
        self.assertIsNotNone(actor_entry)
        self.assertEqual(actor_entry["message"]["text"], "You nod in agreement.")
        self.assertEqual(
            actor_entry["message"]["data"]["actor"]["key"],
            self.player.key,
        )

        witness_entry = self._entry(
            messages,
            "notification.social",
            recipient=witness.key,
            social="nod",
        )
        self.assertIsNotNone(witness_entry)
        self.assertEqual(
            witness_entry["message"]["text"],
            "Joe nods in agreement.",
        )
        self.assertEqual(
            witness_entry["message"]["data"]["actor"]["key"],
            self.player.key,
        )
        self.assertIsNone(
            self._entry(messages, "affect.social", recipient=witness.key)
        )

    def test_targeted_social_emits_actor_target_and_witness_events(self):
        self._social(
            "nod",
            msg_targeted_self="You nod at {{ target }}.",
            msg_targeted_target="{{ Actor }} nods at you.",
            msg_targeted_other="{{ Actor }} nods at {{ target }}.",
        )
        target = self._online_player("River")
        witness = self._online_player("Witness")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod river")

        actor_entry = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="nod",
        )
        self.assertIsNotNone(actor_entry)
        self.assertEqual(actor_entry["message"]["text"], "You nod at River.")
        self.assertEqual(
            actor_entry["message"]["data"]["target"]["key"],
            target.key,
        )

        target_entry = self._entry(
            messages,
            "affect.social",
            recipient=target.key,
            social="nod",
        )
        self.assertIsNotNone(target_entry)
        self.assertEqual(target_entry["message"]["text"], "Joe nods at you.")
        self.assertEqual(
            target_entry["message"]["data"]["actor"]["key"],
            self.player.key,
        )
        self.assertEqual(
            target_entry["message"]["data"]["target"]["key"],
            target.key,
        )

        witness_entry = self._entry(
            messages,
            "notification.social",
            recipient=witness.key,
            social="nod",
        )
        self.assertIsNotNone(witness_entry)
        self.assertEqual(
            witness_entry["message"]["text"],
            "Joe nods at River.",
        )
        self.assertEqual(
            witness_entry["message"]["data"]["target"]["key"],
            target.key,
        )
        self.assertIsNone(
            self._entry(
                messages,
                "notification.social",
                recipient=target.key,
                social="nod",
            )
        )

    def test_target_argument_falls_back_to_targetless_when_target_messages_absent(self):
        self._social(
            "shrug",
            msg_targetless_self="You shrug vaguely.",
            msg_targetless_other="{{ Actor }} shrugs vaguely.",
            msg_targeted_self=None,
            msg_targeted_target=None,
            msg_targeted_other=None,
        )
        apparent_target = self._online_player("River")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "shrug river")

        actor_entry = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="shrug",
        )
        self.assertIsNotNone(actor_entry)
        self.assertEqual(actor_entry["message"]["text"], "You shrug vaguely.")
        self.assertNotIn("target", actor_entry["message"]["data"])

        witness_entry = self._entry(
            messages,
            "notification.social",
            recipient=apparent_target.key,
            social="shrug",
        )
        self.assertIsNotNone(witness_entry)
        self.assertEqual(
            witness_entry["message"]["text"],
            "Joe shrugs vaguely.",
        )
        self.assertIsNone(
            self._entry(
                messages,
                "affect.social",
                recipient=apparent_target.key,
                social="shrug",
            )
        )

    def test_target_only_social_requires_and_accepts_a_target(self):
        self._social(
            "beckon",
            msg_targetless_self=None,
            msg_targetless_other=None,
            msg_targeted_self="You beckon to {{ target }}.",
            msg_targeted_target="{{ Actor }} beckons to you.",
            msg_targeted_other="{{ Actor }} beckons to {{ target }}.",
        )
        target = self._online_player("River")

        with capture_game_messages() as missing_messages:
            dispatch_text_command(self.player.id, "beckon")

        error_entry = self._entry(
            missing_messages,
            "cmd.dosocial.error",
            recipient=self.player.key,
        )
        self.assertIsNotNone(error_entry)
        self.assertEqual(error_entry["message"]["text"], "A target is required.")
        self.assertIsNone(
            self._entry(missing_messages, "cmd.dosocial.success")
        )

        with capture_game_messages() as targeted_messages:
            dispatch_text_command(self.player.id, "beckon river")

        self.assertIsNotNone(
            self._entry(
                targeted_messages,
                "cmd.dosocial.success",
                recipient=self.player.key,
                social="beckon",
            )
        )
        self.assertIsNotNone(
            self._entry(
                targeted_messages,
                "affect.social",
                recipient=target.key,
                social="beckon",
            )
        )

    def test_exact_social_wins_then_prefix_uses_priority_and_command_order(self):
        self._social(
            "ponder",
            priority=1,
            msg_targetless_self="You ponder exactly.",
        )
        self._social(
            "pondering",
            priority=100,
            msg_targetless_self="You choose the high-priority prefix.",
        )
        self._social("smirk", priority=5)
        self._social("smile", priority=5)

        with capture_game_messages() as exact_messages:
            dispatch_text_command(self.player.id, "ponder")

        exact = self._entry(
            exact_messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
        )
        self.assertEqual(exact["message"]["data"]["social"], "ponder")
        self.assertEqual(exact["message"]["text"], "You ponder exactly.")

        with capture_game_messages() as priority_messages:
            dispatch_text_command(self.player.id, "pond")

        priority = self._entry(
            priority_messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
        )
        self.assertEqual(priority["message"]["data"]["social"], "pondering")

        with capture_game_messages() as ordered_messages:
            dispatch_text_command(self.player.id, "smi")

        ordered = self._entry(
            ordered_messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
        )
        self.assertEqual(ordered["message"]["data"]["social"], "smile")

    def test_socials_lists_rows_alphabetically(self):
        with capture_game_messages() as empty_messages:
            dispatch_text_command(self.player.id, "socials")

        empty = self._entry(
            empty_messages,
            "cmd.socials.success",
            recipient=self.player.key,
        )
        self.assertIsNotNone(empty)
        self.assertEqual(empty["message"]["data"]["socials"], [])
        self.assertEqual(empty["message"]["text"], "No socials defined.")

        with self.captureOnCommitCallbacks(execute=True):
            # Listing is alphabetical even though abbreviation resolution
            # uses descending priority.
            self._social("wave", priority=100)
            self._social("nod", priority=10)
            self._social("bow", priority=1)

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "socials")

        listed = self._entry(
            messages,
            "cmd.socials.success",
            recipient=self.player.key,
        )
        self.assertIsNotNone(listed)
        self.assertEqual(
            listed["message"]["data"]["socials"],
            ["bow", "nod", "wave"],
        )
        for command in ("bow", "nod", "wave"):
            self.assertIn(command, listed["message"]["text"])

    def test_social_templates_support_names_titles_pronouns_and_character_state(self):
        self.player.gender = adv_consts.GENDER_MALE
        self.player.title = "the Bold"
        self.player.save(update_fields=["gender", "title"])
        replace_state_snapshot(
            STATE_SCOPE_CHARACTER,
            self.player,
            {"badge": "sun"},
        )

        target = self._online_player("river")
        target.gender = adv_consts.GENDER_NON_BINARY
        target.title = "of the Vale"
        target.save(update_fields=["gender", "title"])
        replace_state_snapshot(
            STATE_SCOPE_CHARACTER,
            target,
            {"oath": "moon"},
        )
        witness = self._online_player("Witness")

        context_template = (
            "{{ actor }}/{{ Actor }}/{{ actor_title }}/{{ actor_state.badge }}/"
            "{{ actor_subject_pronoun }}/{{ actor_object_pronoun }}/"
            "{{ actor_possessive_adjective }}/{{ actor_possessive_pronoun }}/"
            "{{ actor_reflexive_pronoun }} -> "
            "{{ target }}/{{ Target }}/{{ target_title }}/{{ target_state.oath }}/"
            "{{ target_subject_pronoun }}/{{ target_object_pronoun }}/"
            "{{ target_possessive_adjective }}/{{ target_possessive_pronoun }}/"
            "{{ target_reflexive_pronoun }}"
        )
        self._social(
            "honor",
            msg_targeted_self="self: " + context_template,
            msg_targeted_target="target: " + context_template,
            msg_targeted_other="other: " + context_template,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "honor river")

        rendered_context = (
            "Joe/Joe/the Bold/sun/he/him/his/his/himself -> "
            "river/River/of the Vale/moon/they/them/their/theirs/themselves"
        )
        actor_entry = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="honor",
        )
        target_entry = self._entry(
            messages,
            "affect.social",
            recipient=target.key,
            social="honor",
        )
        witness_entry = self._entry(
            messages,
            "notification.social",
            recipient=witness.key,
            social="honor",
        )
        self.assertEqual(actor_entry["message"]["text"], "self: " + rendered_context)
        self.assertEqual(target_entry["message"]["text"], "target: " + rendered_context)
        self.assertEqual(witness_entry["message"]["text"], "other: " + rendered_context)

    def test_target_mute_list_blocks_direct_and_witness_events(self):
        self._social("nod")
        target = self._online_player("River")
        target.gender = adv_consts.GENDER_NON_BINARY
        target.mute_list = "ALICE JOE"
        target.save(update_fields=["gender", "mute_list"])
        witness = self._online_player("Witness")

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod river")

        actor_entry = self._entry(
            messages,
            "cmd.dosocial.error",
            recipient=self.player.key,
        )
        self.assertIsNotNone(actor_entry)
        self.assertIn("want to interact with you", actor_entry["message"]["text"])
        self.assertEqual(actor_entry["message"]["data"]["code"], "target_muted")
        self.assertIsNone(
            self._entry(
                messages,
                "affect.social",
                recipient=target.key,
                social="nod",
            )
        )
        self.assertIsNone(
            self._entry(
                messages,
                "notification.social",
                recipient=witness.key,
                social="nod",
            )
        )

    def test_global_communication_mute_blocks_socials(self):
        self._social("nod")
        witness = self._online_player("Witness")
        self.player.is_muted = True
        self.player.save(update_fields=["is_muted"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod")

        error = self._entry(
            messages,
            "cmd.dosocial.error",
            recipient=self.player.key,
        )
        self.assertIsNotNone(error)
        self.assertEqual(error["message"]["data"]["code"], "muted")
        self.assertIsNone(self._entry(messages, "cmd.dosocial.success"))
        self.assertIsNone(
            self._entry(
                messages,
                "notification.social",
                recipient=witness.key,
            )
        )

    def test_mob_actor_can_execute_a_social_and_notify_players(self):
        self._social(
            "bow",
            msg_targetless_self="You bow.",
            msg_targetless_other="{{ Actor }} bows.",
        )
        guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a brass guard",
            keywords="guard",
        )

        with capture_game_messages() as messages:
            dispatch_text_command_as_mob(guard.id, "bow")

        actor_entry = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=guard.key,
            social="bow",
        )
        self.assertIsNotNone(actor_entry)
        self.assertEqual(actor_entry["message"]["text"], "You bow.")
        self.assertEqual(
            actor_entry["message"]["data"]["actor"]["key"],
            guard.key,
        )

        player_entry = self._entry(
            messages,
            "notification.social",
            recipient=self.player.key,
            social="bow",
        )
        self.assertIsNotNone(player_entry)
        self.assertEqual(player_entry["message"]["text"], "A brass guard bows.")
        self.assertEqual(
            player_entry["message"]["data"]["actor"]["key"],
            guard.key,
        )

    def test_only_exact_target_mob_social_reaction_runs_scripted_social(self):
        self._social("nod")
        self._social("nodding")
        self._social(
            "bow",
            msg_targetless_self="You bow in reply.",
            msg_targetless_other="{{ Actor }} bows in reply.",
        )
        guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
        )
        bystander = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Bystander",
            keywords="bystander",
        )
        mob_content_type = ContentType.objects.get_for_model(Mob)
        for mob in (guard, bystander):
            Trigger.objects.create(
                world=self.world,
                kind=adv_consts.TRIGGER_KIND_EVENT,
                scope=adv_consts.TRIGGER_SCOPE_WORLD,
                target_type=mob_content_type,
                target_id=mob.id,
                event=adv_consts.MOB_REACTION_EVENT_SOCIAL,
                match="nod",
                script="bow",
                display_action_in_room=False,
                gate_delay=0,
            )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod guard")

        self.assertIsNotNone(
            self._entry(
                messages,
                "cmd.dosocial.success",
                recipient=guard.key,
                social="bow",
            )
        )
        self.assertIsNone(
            self._entry(
                messages,
                "cmd.dosocial.success",
                recipient=bystander.key,
                social="bow",
            )
        )
        reaction_notification = self._entry(
            messages,
            "notification.social",
            recipient=self.player.key,
            social="bow",
        )
        self.assertIsNotNone(reaction_notification)
        self.assertEqual(
            reaction_notification["message"]["data"]["actor"]["key"],
            guard.key,
        )

        with capture_game_messages() as near_match_messages:
            dispatch_text_command(self.player.id, "nodding guard")

        self.assertIsNone(
            self._entry(
                near_match_messages,
                "cmd.dosocial.success",
                recipient=guard.key,
                social="bow",
            )
        )

    def test_target_mob_reaction_conditions_ignore_other_runtime_world_mobs(self):
        self._social("nod")
        self._social(
            "bow",
            msg_targetless_self="You bow in reply.",
            msg_targetless_other="{{ Actor }} bows in reply.",
        )
        guard = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="Guard",
            keywords="guard",
        )
        sentinel_definition = MobDefinition.objects.create(
            world=self.world,
            slug="reaction-sentinel",
            name="Reaction Sentinel",
        )
        other_runtime = self.world.create_spawn_world()
        Mob.objects.create(
            world=other_runtime,
            room=self.room,
            definition=sentinel_definition,
            name="Foreign Sentinel",
            keywords="sentinel",
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(Mob),
            target_id=guard.id,
            event=adv_consts.MOB_REACTION_EVENT_SOCIAL,
            match="nod",
            conditions=f"mob_in_room {sentinel_definition.id}",
            script="bow",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as foreign_only_messages:
            dispatch_text_command(self.player.id, "nod guard")

        self.assertIsNone(
            self._entry(
                foreign_only_messages,
                "cmd.dosocial.success",
                recipient=guard.key,
                social="bow",
            )
        )

        Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=sentinel_definition,
            name="Local Sentinel",
            keywords="sentinel",
        )

        with capture_game_messages() as local_messages:
            dispatch_text_command(self.player.id, "nod guard")

        self.assertIsNotNone(
            self._entry(
                local_messages,
                "cmd.dosocial.success",
                recipient=guard.key,
                social="bow",
            )
        )

    def test_instance_runtime_inherits_socials_from_base_world(self):
        self._social(
            "nod",
            msg_targetless_self="You nod in the instance.",
        )
        instance_config = WorldConfig.objects.create()
        instance_template = World.objects.new_world(
            name="Sunken Hold",
            author=self.user,
            config=instance_config,
            instance_of=self.world,
        )
        instance_runtime = instance_template.create_spawn_world()
        self.player.world = instance_runtime
        self.player.room = instance_template.config.starting_room
        self.player.save(update_fields=["world", "room"])

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod")

        actor_entry = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="nod",
        )
        self.assertIsNotNone(actor_entry)
        self.assertEqual(
            actor_entry["message"]["text"],
            "You nod in the instance.",
        )

    def test_social_targets_and_witnesses_are_isolated_to_runtime_world(self):
        self._social(
            "nod",
            msg_targetless_other="{{ Actor }} nods.",
        )
        local_witness = self._online_player("Local")
        other_runtime = self.world.create_spawn_world()
        foreign_player = self._online_player(
            "Outsider",
            world=other_runtime,
            room=self.room,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod")

        self.assertIsNotNone(
            self._entry(
                messages,
                "notification.social",
                recipient=local_witness.key,
                social="nod",
            )
        )
        self.assertIsNone(
            self._entry(
                messages,
                "notification.social",
                recipient=foreign_player.key,
                social="nod",
            )
        )

        with capture_game_messages() as target_messages:
            dispatch_text_command(self.player.id, "nod outsider")

        self.assertIsNotNone(
            self._entry(
                target_messages,
                "cmd.dosocial.error",
                recipient=self.player.key,
            )
        )
        self.assertIsNone(
            self._entry(
                target_messages,
                "affect.social",
                recipient=foreign_player.key,
                social="nod",
            )
        )

    def test_social_cannot_target_self_or_an_invisible_player(self):
        self._social("nod")
        invisible = self._online_player("Shade")
        invisible.is_invisible = True
        invisible.save(update_fields=["is_invisible"])

        for command in ("nod joe", "nod shade"):
            with self.subTest(command=command):
                with capture_game_messages() as messages:
                    dispatch_text_command(self.player.id, command)

                error = self._entry(
                    messages,
                    "cmd.dosocial.error",
                    recipient=self.player.key,
                )
                self.assertIsNotNone(error)
                self.assertEqual(
                    error["message"]["data"]["code"],
                    "target_not_found",
                )
                self.assertIsNone(self._entry(messages, "affect.social"))

    def test_social_cannot_target_an_invisible_mob(self):
        self._social("nod")
        invisible = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            name="a hidden shade",
            keywords="shade",
            is_invisible=True,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "nod shade")

        error = self._entry(
            messages,
            "cmd.dosocial.error",
            recipient=self.player.key,
        )
        self.assertIsNotNone(error)
        self.assertEqual(error["message"]["data"]["code"], "target_not_found")
        self.assertIsNone(
            self._entry(
                messages,
                "affect.social",
                social="nod",
            )
        )
        self.assertTrue(Mob.objects.filter(pk=invisible.pk).exists())

    def test_static_command_takes_precedence_over_same_named_social(self):
        self._social(
            "emote",
            msg_targetless_self="This social must not run.",
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "emote smiles deliberately.")

        self.assertIsNotNone(
            self._entry(
                messages,
                "cmd.emote.success",
                recipient=self.player.key,
            )
        )
        self.assertIsNone(self._entry(messages, "cmd.dosocial.success"))

    def test_dynamic_ability_takes_precedence_over_same_named_social(self):
        self._social(
            "focus",
            msg_targetless_self="This social must not run.",
        )
        AbilityDefinition.objects.create(
            world=self.world,
            slug="inner-focus",
            name="Inner Focus",
            command_verbs=["focus"],
            target={
                "type": "self",
                "default": "self",
                "allow_out_of_combat": True,
            },
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cast_time={},
            cooldown={"rounds": 0},
            components=[],
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "focus")

        ability_error = self._entry(
            messages,
            "cmd.ability.error",
            recipient=self.player.key,
        )
        self.assertIsNotNone(ability_error)
        self.assertEqual(
            ability_error["message"]["data"]["code"],
            "ability_unknown",
        )
        self.assertIsNone(self._entry(messages, "cmd.dosocial.success"))

    def test_contextual_trigger_takes_precedence_over_matching_social(self):
        self._social(
            "touch",
            msg_targetless_self="This social must not run.",
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            match="touch altar",
            script="/cmd room -- /echo -- The trigger wins.",
            display_action_in_room=True,
            gate_delay=0,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        echo_entry = self._entry(messages, "cmd./echo.success")
        self.assertIsNotNone(echo_entry)
        self.assertIn("The trigger wins.", echo_entry["message"]["text"])
        self.assertIsNone(self._entry(messages, "cmd.dosocial.success"))

    def test_trigger_script_social_does_not_resolve_as_dynamic_ability(self):
        self._social(
            "focus",
            msg_targetless_self="You focus socially.",
        )
        AbilityDefinition.objects.create(
            world=self.world,
            slug="inner-focus",
            name="Inner Focus",
            command_verbs=["focus"],
            target={
                "type": "self",
                "default": "self",
                "allow_out_of_combat": True,
            },
            availability={"classes": [], "min_level": 1},
            requirements={},
            cost={},
            cast_time={},
            cooldown={"rounds": 0},
            components=[],
        )
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            match="touch altar",
            script="focus",
            display_action_in_room=False,
            gate_delay=0,
        )

        with capture_game_messages() as messages:
            dispatch_text_command(self.player.id, "touch altar")

        social = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="focus",
        )
        self.assertIsNotNone(social)
        self.assertEqual(social["message"]["text"], "You focus socially.")
        self.assertIsNone(self._entry(messages, "cmd.ability.error"))

    def test_social_cache_is_invalidated_on_save_and_delete(self):
        with self.captureOnCommitCallbacks(execute=True):
            social = self._social(
                "wave",
                msg_targetless_self="Version one.",
            )

        with capture_game_messages() as initial_messages:
            dispatch_text_command(self.player.id, "wave")

        initial = self._entry(
            initial_messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="wave",
        )
        self.assertEqual(initial["message"]["text"], "Version one.")

        social.msg_targetless_self = "Version two."
        with self.captureOnCommitCallbacks(execute=True):
            social.save(update_fields=["msg_targetless_self", "modified_ts"])

        with capture_game_messages() as updated_messages:
            dispatch_text_command(self.player.id, "wave")

        updated = self._entry(
            updated_messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="wave",
        )
        self.assertEqual(updated["message"]["text"], "Version two.")

        with self.captureOnCommitCallbacks(execute=True):
            social.delete()

        with capture_game_messages() as deleted_messages:
            dispatch_text_command(self.player.id, "wave")

        self.assertIsNone(
            self._entry(deleted_messages, "cmd.dosocial.success")
        )
        self.assertIsNotNone(
            self._entry(
                deleted_messages,
                "cmd.text.error",
                recipient=self.player.key,
            )
        )

    def test_rolled_back_social_does_not_poison_shared_catalog(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self._social("wave")
        # WorldTestCase itself owns an outer transaction. Temporarily model a
        # normal game worker read so this assertion really populates the shared
        # cache before entering the rollback savepoint below.
        with patch.object(connection, "in_atomic_block", False):
            self.assertIsNotNone(resolve_social_for_command(self.world, "wave"))

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self._social("rollback")
                # Exercise a same-transaction catalog read before the savepoint
                # rolls back. It must not publish uncommitted rows globally.
                resolve_social_for_command(self.world, "rollback")
                raise RuntimeError("roll back the social savepoint")

        self.assertFalse(
            Social.objects.filter(world=self.world, cmd="rollback").exists()
        )
        with patch.object(connection, "in_atomic_block", False):
            self.assertIsNone(resolve_social_for_command(self.world, "rollback"))

    def test_exact_social_lookup_uses_at_most_one_catalog_query_at_scale(self):
        Social.objects.bulk_create(
            [
                Social(
                    world=self.world,
                    cmd=f"gesture{index:03d}",
                    priority=index % 5,
                    msg_targetless_self=f"Gesture {index}.",
                    msg_targetless_other="{{ Actor }} gestures.",
                    msg_targeted_self="You gesture at {{ target }}.",
                    msg_targeted_target="{{ Actor }} gestures at you.",
                    msg_targeted_other="{{ Actor }} gestures at {{ target }}.",
                )
                for index in range(60)
            ]
        )

        with capture_game_messages() as messages:
            with CaptureQueriesContext(connection) as queries:
                dispatch_text_command(self.player.id, "gesture059")

        actor_entry = self._entry(
            messages,
            "cmd.dosocial.success",
            recipient=self.player.key,
            social="gesture059",
        )
        self.assertIsNotNone(actor_entry)
        social_table = Social._meta.db_table.lower()
        social_queries = [
            query
            for query in queries.captured_queries
            if social_table in query["sql"].lower()
        ]
        self.assertLessEqual(len(social_queries), 1)
        ability_table = AbilityDefinition._meta.db_table.lower()
        ability_queries = [
            query
            for query in queries.captured_queries
            if ability_table in query["sql"].lower()
        ]
        self.assertLessEqual(len(ability_queries), 1)
        trigger_table = Trigger._meta.db_table.lower()
        trigger_queries = [
            query
            for query in queries.captured_queries
            if trigger_table in query["sql"].lower()
        ]
        self.assertLessEqual(len(trigger_queries), 1)
