"""doc 9 "Resilience" row: queue backlog / repeated processing failure ->
retry then DLQ (doc 2.3 "durable message queue with retry and DLQ")."""
import pytest

from geniebot.queue.memory_queue import MemoryQueue


@pytest.mark.asyncio
async def test_message_moves_to_dlq_after_max_attempts():
    queue = MemoryQueue(max_attempts=3)
    await queue.enqueue({"incident_id": "i1"})

    for _ in range(3):
        message = await queue.dequeue(wait_seconds=0.1)
        assert message is not None
        await queue.nack(message.receipt_handle, requeue=True)

    assert queue.qsize() == 0
    dlq = await queue.dlq_messages()
    assert len(dlq) == 1
    assert dlq[0].body == {"incident_id": "i1"}


@pytest.mark.asyncio
async def test_message_requeued_and_recovered_before_max_attempts():
    queue = MemoryQueue(max_attempts=3)
    await queue.enqueue({"incident_id": "i1"})

    first = await queue.dequeue(wait_seconds=0.1)
    await queue.nack(first.receipt_handle, requeue=True)

    second = await queue.dequeue(wait_seconds=0.1)
    assert second is not None
    await queue.ack(second.receipt_handle)

    assert await queue.dlq_messages() == []


@pytest.mark.asyncio
async def test_backlog_of_many_messages_all_drain():
    queue = MemoryQueue()
    for i in range(50):
        await queue.enqueue({"incident_id": f"i{i}"})
    assert queue.qsize() == 50

    drained = 0
    while queue.qsize() > 0:
        message = await queue.dequeue(wait_seconds=0.1)
        await queue.ack(message.receipt_handle)
        drained += 1
    assert drained == 50
