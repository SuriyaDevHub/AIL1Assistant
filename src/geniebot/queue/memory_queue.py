"""In-process queue for local/dev/test. Not persisted across restarts -
that's an explicit trade-off for zero-config local runs; SQS
(sqs_queue.py) is the durable backend for real environments.
"""
from __future__ import annotations

import asyncio
import uuid

from geniebot.queue.base import Queue, QueueMessage


class MemoryQueue(Queue):
    def __init__(self, *, max_attempts: int = 3):
        self._queue: asyncio.Queue[QueueMessage] = asyncio.Queue()
        self._in_flight: dict[str, QueueMessage] = {}
        self._dlq: list[QueueMessage] = []
        self._max_attempts = max_attempts

    async def enqueue(self, body: dict) -> str:
        message_id = str(uuid.uuid4())
        await self._queue.put(QueueMessage(message_id=message_id, body=body, receipt_handle="", attempt=0))
        return message_id

    async def dequeue(self, *, wait_seconds: float = 5.0) -> QueueMessage | None:
        try:
            raw = await asyncio.wait_for(self._queue.get(), timeout=wait_seconds)
        except TimeoutError:
            return None
        receipt_handle = str(uuid.uuid4())
        message = QueueMessage(
            message_id=raw.message_id, body=raw.body, receipt_handle=receipt_handle, attempt=raw.attempt + 1
        )
        self._in_flight[receipt_handle] = message
        return message

    async def ack(self, receipt_handle: str) -> None:
        self._in_flight.pop(receipt_handle, None)

    async def nack(self, receipt_handle: str, *, requeue: bool = True) -> None:
        message = self._in_flight.pop(receipt_handle, None)
        if message is None:
            return
        if requeue and message.attempt < self._max_attempts:
            await self._queue.put(message)
        else:
            self._dlq.append(message)

    async def dlq_messages(self) -> list[QueueMessage]:
        return list(self._dlq)

    def qsize(self) -> int:
        return self._queue.qsize()
