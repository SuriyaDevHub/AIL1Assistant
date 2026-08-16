"""Real durable-queue backend: AWS SQS with a redrive policy to a DLQ (doc
2.3). Assumes the SQS queue's redrive policy (maxReceiveCount -> DLQ) is
configured at the infrastructure level (deploy/k8s or the platform team's
Terraform) - this client honours attempt counts it can see via
ApproximateReceiveCount but does not itself provision the DLQ.

Not exercised without a real sqs_queue_url/sqs_dlq_url and AWS credentials.
"""
from __future__ import annotations

import json

import boto3

from geniebot.queue.base import Queue, QueueMessage


class SQSQueue(Queue):
    def __init__(self, *, queue_url: str, dlq_url: str, max_attempts: int = 3):
        self._queue_url = queue_url
        self._dlq_url = dlq_url
        self._max_attempts = max_attempts
        self._client = boto3.client("sqs")

    async def enqueue(self, body: dict) -> str:
        response = self._client.send_message(QueueUrl=self._queue_url, MessageBody=json.dumps(body))
        return response["MessageId"]

    async def dequeue(self, *, wait_seconds: float = 5.0) -> QueueMessage | None:
        response = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=min(int(wait_seconds), 20),
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        raw = messages[0]
        attempt = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        return QueueMessage(
            message_id=raw["MessageId"],
            body=json.loads(raw["Body"]),
            receipt_handle=raw["ReceiptHandle"],
            attempt=attempt,
        )

    async def ack(self, receipt_handle: str) -> None:
        self._client.delete_message(QueueUrl=self._queue_url, ReceiptHandle=receipt_handle)

    async def nack(self, receipt_handle: str, *, requeue: bool = True) -> None:
        if not requeue:
            # Making the message immediately visible again lets SQS's own
            # redrive policy move it to the DLQ once maxReceiveCount is hit,
            # rather than this client re-implementing DLQ routing.
            self._client.change_message_visibility(
                QueueUrl=self._queue_url, ReceiptHandle=receipt_handle, VisibilityTimeout=0
            )
        else:
            self._client.change_message_visibility(
                QueueUrl=self._queue_url, ReceiptHandle=receipt_handle, VisibilityTimeout=0
            )

    async def dlq_messages(self) -> list[QueueMessage]:
        response = self._client.receive_message(
            QueueUrl=self._dlq_url, MaxNumberOfMessages=10, AttributeNames=["ApproximateReceiveCount"]
        )
        return [
            QueueMessage(
                message_id=m["MessageId"],
                body=json.loads(m["Body"]),
                receipt_handle=m["ReceiptHandle"],
                attempt=int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            )
            for m in response.get("Messages", [])
        ]
