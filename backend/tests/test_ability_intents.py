from types import SimpleNamespace

from django.test import SimpleTestCase

from spawns.ability_intents import (
    ability_intent_turn_priority,
    prioritize_ready_interrupts,
)


class AbilityIntentPriorityTests(SimpleTestCase):
    @staticmethod
    def _pending(
        target_key,
        *,
        priority="interrupt",
        status=None,
        rounds_remaining=0,
    ):
        pending = {
            "ability": "test-ability",
            "target": {"type": target_key[0], "id": target_key[1]},
            "cast_rounds_remaining": rounds_remaining,
        }
        if priority:
            pending["turn_priority"] = priority
        if status:
            pending["status"] = status
        return pending

    def test_turn_priority_is_derived_from_interrupt_component(self):
        interrupt = SimpleNamespace(components=[{"type": "interrupt"}])
        ordinary = SimpleNamespace(components=[{"type": "damage"}])

        self.assertEqual(ability_intent_turn_priority(interrupt), "interrupt")
        self.assertIsNone(ability_intent_turn_priority(ordinary))

    def test_ready_interrupts_are_grouped_immediately_before_target(self):
        first_interrupter = ("player", 1)
        bystander = ("mob", 2)
        target = ("mob", 3)
        second_interrupter = ("player", 4)
        tail = ("mob", 5)
        order = [first_interrupter, bystander, target, second_interrupter, tail]
        pending_by_actor = {
            first_interrupter: self._pending(target),
            bystander: {},
            target: self._pending(
                first_interrupter,
                priority=None,
                status="casting",
            ),
            second_interrupter: self._pending(target),
            tail: {},
        }

        self.assertEqual(
            prioritize_ready_interrupts(
                order,
                pending_by_actor=pending_by_actor,
            ),
            [bystander, first_interrupter, second_interrupter, target, tail],
        )

    def test_unready_interrupt_or_uncommitted_target_keeps_stored_order(self):
        target = ("mob", 1)
        interrupter = ("player", 2)
        order = [target, interrupter]
        cases = {
            "target queued": {
                target: self._pending(
                    interrupter,
                    priority=None,
                    status="queued",
                    rounds_remaining=1,
                ),
                interrupter: self._pending(target),
            },
            "interrupt winding up": {
                target: self._pending(
                    interrupter,
                    priority=None,
                    status="casting",
                ),
                interrupter: self._pending(target, rounds_remaining=1),
            },
            "ordinary instant ability": {
                target: self._pending(
                    interrupter,
                    priority=None,
                    status="casting",
                ),
                interrupter: self._pending(target, priority=None),
            },
        }

        for label, pending_by_actor in cases.items():
            with self.subTest(label=label):
                self.assertEqual(
                    prioritize_ready_interrupts(
                        order,
                        pending_by_actor=pending_by_actor,
                    ),
                    order,
                )

    def test_mutual_interrupt_cycle_keeps_stored_order(self):
        first = ("player", 1)
        second = ("player", 2)
        order = [first, second]
        pending_by_actor = {
            first: self._pending(second, status="casting"),
            second: self._pending(first, status="casting"),
        }

        self.assertEqual(
            prioritize_ready_interrupts(
                order,
                pending_by_actor=pending_by_actor,
            ),
            order,
        )

    def test_long_interrupt_chain_is_ordered_iteratively(self):
        actors = [("player", actor_id) for actor_id in range(1, 1501)]
        pending_by_actor = {
            actor: self._pending(
                actors[index + 1],
                status="casting",
            )
            for index, actor in enumerate(actors[:-1])
        }
        pending_by_actor[actors[-1]] = self._pending(
            actors[0],
            priority=None,
            status="casting",
        )

        self.assertEqual(
            prioritize_ready_interrupts(
                reversed(actors),
                pending_by_actor=pending_by_actor,
            ),
            actors,
        )
