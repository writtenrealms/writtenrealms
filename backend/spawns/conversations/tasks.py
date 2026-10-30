from celery import shared_task

from spawns.conversations.service import finish, poll, prune
from spawns.models import MobConversationFocus


@shared_task(ignore_result=True, queue='celery', soft_time_limit=10, time_limit=15)
def finish_reply(request_id):
    player_id = MobConversationFocus.objects.filter(pending_request_id=request_id).values_list('player_id', flat=True).first()
    if player_id is not None:
        finish(player_id, request_id=request_id)


@shared_task(ignore_result=True, soft_time_limit=10, time_limit=15)
def finish_conversations():
    poll()


@shared_task(ignore_result=True)
def prune_conversations():
    prune()
