from django.contrib import admin
from django.test import TestCase

from quests.models import (
    QuestArcTemplate,
    QuestJournalEntry,
    QuestInstance,
    QuestObjectiveState,
    QuestOfferState,
    QuestTemplate,
)


class QuestAdminRegistrationTests(TestCase):
    def test_wr2_quest_models_are_registered_in_admin(self):
        self.assertIn(QuestArcTemplate, admin.site._registry)
        self.assertIn(QuestTemplate, admin.site._registry)
        self.assertIn(QuestInstance, admin.site._registry)
        self.assertIn(QuestObjectiveState, admin.site._registry)
        self.assertIn(QuestJournalEntry, admin.site._registry)
        self.assertIn(QuestOfferState, admin.site._registry)
