from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class FounderProgram(models.Model):
    """Operator policy, intentionally outside builder-editable manifests."""
    world = models.OneToOneField('worlds.World', on_delete=models.PROTECT,
                                 related_name='founder_program')
    enabled = models.BooleanField(default=False)
    claim_deadline = models.DateTimeField(null=True, blank=True, db_index=True)


class FounderPass(models.Model):
    program = models.ForeignKey(FounderProgram, on_delete=models.PROTECT,
                                related_name='passes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='founder_passes')
    # Immutable issuance identity makes a grant retry safe even after transfer.
    issued_email = models.EmailField(max_length=255)
    kind = models.CharField(max_length=10, choices=[('direct', 'Direct'), ('gift', 'Gift')])
    parent = models.OneToOneField('self', null=True, blank=True,
                                  on_delete=models.PROTECT, related_name='gift')
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_reason = models.CharField(max_length=12, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['program', 'user'], name='founder_one_pass_per_user'),
            models.UniqueConstraint('program', Lower('issued_email'), name='founder_one_issue_per_email'),
            models.CheckConstraint(condition=(
                models.Q(kind='direct', parent__isnull=True) |
                models.Q(kind='gift', parent__isnull=False, claimed_at__isnull=False)),
                name='founder_pass_kind_parent'),
            models.CheckConstraint(condition=(
                models.Q(claimed_at__isnull=True, claim_reason='') |
                (models.Q(claimed_at__isnull=False) & models.Q(
                    claim_reason__in=['self', 'transfer', 'gift', 'deadline']))),
                name='founder_claim_consistent'),
        ]
        indexes = [models.Index(fields=['program', 'id'], condition=models.Q(
            claimed_at__isnull=True), name='founder_unclaimed')]


class PassTransfer(models.Model):
    founder_pass = models.ForeignKey(FounderPass, on_delete=models.CASCADE,
                                     related_name='transfers')
    source_email = models.EmailField(max_length=255)
    destination = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    destination_email = models.EmailField(max_length=255)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    active = models.BooleanField(default=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['founder_pass'],
            condition=models.Q(active=True), name='founder_one_pending_transfer')]


class FounderEvent(models.Model):
    founder_pass = models.ForeignKey(FounderPass, on_delete=models.PROTECT,
                                     related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                              on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
