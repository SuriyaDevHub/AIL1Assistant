"""Queue abstraction - doc 2.3: "Durable message queue with retry and DLQ -
Decouples ingestion from agent processing". Sits between ingestion (which
enqueues one message per failure-log incident) and the worker (which
dequeues, processes, and acks/nacks).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    body: dict
    receipt_handle: str
    attempt: int


class Queue(ABC):
    @abstractmethod
    async def enqueue(self, body: dict) -> str:
        """Returns the message_id."""

    @abstractmethod
    async def dequeue(self, *, wait_seconds: float = 5.0) -> QueueMessage | None:
        """Returns None if no message became available within wait_seconds."""

    @abstractmethod
    async def ack(self, receipt_handle: str) -> None: ...

    @abstractmethod
    async def nack(self, receipt_handle: str, *, requeue: bool = True) -> None: ...

    @abstractmethod
    async def dlq_messages(self) -> list[QueueMessage]: ...
