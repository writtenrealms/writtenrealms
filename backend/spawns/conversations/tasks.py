from celery import shared_task

from spawns.conversations.service import poll, prune


@shared_task(ignore_result=True, soft_time_limit=10, time_limit=15)
def finish_conversations():
    poll()


@shared_task(ignore_result=True)
def prune_conversations():
    prune()
