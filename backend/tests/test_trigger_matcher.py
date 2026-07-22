from django.test import SimpleTestCase

from spawns import trigger_matcher


class TestTriggerMatcher(SimpleTestCase):
    def test_parentheses_and_not_precedence(self):
        expression = "hello and (traveler or friend) and not enemy"

        self.assertTrue(
            trigger_matcher.evaluate_match_expression(
                expression,
                term_matcher=lambda term: trigger_matcher.phrase_term_match(
                    "hello friend",
                    term,
                ),
            )
        )
        self.assertFalse(
            trigger_matcher.evaluate_match_expression(
                expression,
                term_matcher=lambda term: trigger_matcher.phrase_term_match(
                    "hello enemy friend",
                    term,
                ),
            )
        )

    def test_symbolic_operators_alias_keywords(self):
        keyword_expression = "touch altar and (pray or kneel)"
        symbolic_expression = "touch altar + (pray | kneel)"
        candidate = "touch altar pray"

        keyword_result = trigger_matcher.evaluate_match_expression(
            keyword_expression,
            term_matcher=lambda term: trigger_matcher.phrase_term_match(candidate, term),
        )
        symbolic_result = trigger_matcher.evaluate_match_expression(
            symbolic_expression,
            term_matcher=lambda term: trigger_matcher.phrase_term_match(candidate, term),
        )

        self.assertTrue(keyword_result)
        self.assertEqual(keyword_result, symbolic_result)

    def test_quoted_literals_preserve_spaces(self):
        expression = '"ancient archive" and not "forbidden vault"'
        self.assertTrue(
            trigger_matcher.evaluate_match_expression(
                expression,
                term_matcher=lambda term: trigger_matcher.phrase_term_match(
                    "the ancient archive is open",
                    term,
                ),
            )
        )

    def test_invalid_expression_raises(self):
        with self.assertRaises(trigger_matcher.MatchExpressionError):
            trigger_matcher.validate_match_expression("hello and (friend or")
