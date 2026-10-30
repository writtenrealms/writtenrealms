"""Best-effort task handoffs after commit; database scans recover lost notices."""
import logging


logger = logging.getLogger(__name__)
BROKER_TIMEOUT = 1.0


def publish(task, request_id, *, queue):
    # Use a dedicated, bounded connection instead of waiting for a pooled one.
    # A broker outage must not hold a player command or inference worker open.
    try:
        with task.app.connection_for_write(
            connect_timeout=BROKER_TIMEOUT,
            transport_options={
                **(task.app.conf.broker_transport_options or {}),
                'read_timeout': BROKER_TIMEOUT,
                'write_timeout': BROKER_TIMEOUT,
            },
        ) as connection:
            connection.ensure_connection(max_retries=0)
            task.apply_async(args=[str(request_id)], queue=queue, connection=connection,
                             expires=15, retry=False)
    except Exception:
        # Broker exceptions may contain credentials. The saved job/result is
        # still due for recovery, even if publication succeeded ambiguously.
        logger.warning('Could not confirm %s notification; recovery polling remains active.', task.name)


def request_ready(request_id):
    from inference.tasks import execute_request
    publish(execute_request, request_id, queue='inference')


def result_ready(request_id):
    from spawns.conversations.tasks import finish_reply
    publish(finish_reply, request_id, queue='celery')
