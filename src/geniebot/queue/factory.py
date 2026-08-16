from __future__ import annotations

from functools import lru_cache

from geniebot.queue.base import Queue
from geniebot.settings import get_settings


@lru_cache
def get_queue() -> Queue:
    settings = get_settings()
    if settings.queue_backend == "sqs":
        from geniebot.queue.sqs_queue import SQSQueue

        return SQSQueue(queue_url=settings.sqs_queue_url, dlq_url=settings.sqs_dlq_url)
    from geniebot.queue.memory_queue import MemoryQueue

    return MemoryQueue()
