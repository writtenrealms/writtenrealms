import uuid

from django.db import models


class InferenceControl(models.Model):
    """One installation control and lock; credentials never enter this table."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    generation = models.PositiveBigIntegerField(default=0)
    configuration = models.JSONField(default=dict)
    circuit_until = models.DateTimeField(null=True)
    circuit_reason = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='inference_single_control')]


class BudgetBucket(models.Model):
    scope = models.CharField(max_length=80)
    period = models.DateField()
    reserved_micro = models.PositiveBigIntegerField(default=0)
    spent_micro = models.PositiveBigIntegerField(default=0)
    requests = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scope', 'period'], name='inference_bucket_identity')]
        indexes = [models.Index(fields=['period'], name='inference_bucket_period_idx')]


class InferenceRequest(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued'
        DISPATCHING = 'dispatching'
        SUCCEEDED = 'succeeded'
        INVALID = 'invalid'
        UNCERTAIN = 'uncertain'
        CANCELLED = 'cancelled'
        DISCARDED = 'discarded'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=64)
    purpose = models.CharField(max_length=24)
    owner = models.CharField(max_length=40)
    user_id = models.PositiveBigIntegerField()
    world_id = models.PositiveBigIntegerField()
    provider = models.CharField(max_length=16)
    profile = models.JSONField()
    generation = models.PositiveBigIntegerField()
    payload = models.JSONField(default=dict)
    reservation_micro = models.PositiveBigIntegerField()
    charged_micro = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    enqueue_after = models.DateTimeField()
    lease_until = models.DateTimeField(null=True)
    dispatch_token = models.UUIDField(null=True)
    completed_at = models.DateTimeField(null=True)
    result = models.JSONField(null=True)
    usage = models.JSONField(null=True)
    error_code = models.CharField(max_length=40, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'enqueue_after'], name='inference_queue_idx'),
            models.Index(fields=['status', 'lease_until'], name='inference_lease_idx'),
            models.Index(fields=['owner', 'status'], name='inference_owner_idx'),
            models.Index(fields=['completed_at'], name='inference_retention_idx'),
            models.Index(fields=['completed_at'], condition=models.Q(result__isnull=False),
                         name='inference_result_expiry_idx'),
        ]


class BudgetAllocation(models.Model):
    request = models.ForeignKey(InferenceRequest, on_delete=models.CASCADE, related_name='allocations')
    bucket = models.ForeignKey(BudgetBucket, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['request', 'bucket'], name='inference_allocation_identity')]
