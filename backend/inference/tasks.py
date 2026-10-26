from celery import shared_task
from billiard.exceptions import SoftTimeLimitExceeded

from inference import providers, service


@shared_task(ignore_result=True, queue='inference', soft_time_limit=35, time_limit=40)
def execute_request(request_id):
    request = service.claim(request_id)
    if request is None:
        return
    try:
        outcome = providers.perform(request.provider, request.profile, request.payload)
    except SoftTimeLimitExceeded:
        outcome = providers.Outcome(error='provider_timeout')
    # No transaction or game lock spans the provider call. A crash at any point
    # after claim retains the full reservation and is repaired by dispatch_batch.
    service.settle(request.pk, request.dispatch_token, outcome)


@shared_task(ignore_result=True, soft_time_limit=10, time_limit=15)
def dispatch_requests():
    for request_id in service.dispatch_batch():
        # Publication may fail; the database outbox remains due after 15s.
        execute_request.apply_async(args=[request_id], queue='inference', expires=15, retry=False)


@shared_task(ignore_result=True)
def prune_requests():
    service.prune()
