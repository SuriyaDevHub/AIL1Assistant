"""S3 event source - doc 2.2 step 1-2: "Genie Bot writes execution log to S3
working directory... S3 event notification triggers the ingestion service."

Two implementations behind the same StorageEventSource interface:
- LocalFilesystemEventSource: polls a local directory, for zero-config
  local/dev/test runs (STORAGE_BACKEND=local).
- S3EventSource: polls S3 ListObjectsV2 under the working prefix
  (STORAGE_BACKEND=s3). True push-based S3 event notifications require
  wiring an SNS/SQS subscription at the platform level, which this
  implementation deliberately avoids depending on so the pipeline still
  runs with only S3 read access - swap to a push-based listener consuming
  S3->SQS notifications for lower latency once that's provisioned.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class StorageEvent:
    bucket: str
    key: str
    size: int
    event_time: datetime


class StorageEventSource(ABC):
    @abstractmethod
    async def watch(self, *, poll_interval: float = 5.0) -> AsyncIterator[StorageEvent]:
        """Yields one StorageEvent per new/changed object under the working prefix."""
        if False:
            yield  # pragma: no cover - makes this an async generator for subclasses' type checking

    @abstractmethod
    async def read_object(self, key: str) -> str: ...


class LocalFilesystemEventSource(StorageEventSource):
    def __init__(self, *, root: str, bucket_name: str, prefix: str = ""):
        self._root = Path(root)
        self._bucket_name = bucket_name
        self._prefix = prefix
        self._seen: dict[str, float] = {}
        self._root.mkdir(parents=True, exist_ok=True)

    async def watch(self, *, poll_interval: float = 5.0) -> AsyncIterator[StorageEvent]:
        import asyncio

        watch_dir = self._root / self._prefix
        watch_dir.mkdir(parents=True, exist_ok=True)
        while True:
            for path in sorted(watch_dir.rglob("*")):
                if not path.is_file():
                    continue
                mtime = path.stat().st_mtime
                key = str(path.relative_to(self._root)).replace("\\", "/")
                if self._seen.get(key) == mtime:
                    continue
                self._seen[key] = mtime
                yield StorageEvent(
                    bucket=self._bucket_name,
                    key=key,
                    size=path.stat().st_size,
                    event_time=datetime.fromtimestamp(mtime, tz=UTC),
                )
            await asyncio.sleep(poll_interval)

    async def read_object(self, key: str) -> str:
        return (self._root / key).read_text(encoding="utf-8")

    def write_object(self, key: str, content: str) -> None:
        """Test/demo helper - simulates Genie Bot writing an execution log."""
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class S3EventSource(StorageEventSource):
    def __init__(self, *, bucket: str, prefix: str = ""):
        import boto3

        self._bucket = bucket
        self._prefix = prefix
        self._client = boto3.client("s3")
        self._seen: dict[str, str] = {}  # key -> etag

    async def watch(self, *, poll_interval: float = 5.0) -> AsyncIterator[StorageEvent]:
        import asyncio

        while True:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    etag = obj["ETag"]
                    if self._seen.get(key) == etag:
                        continue
                    self._seen[key] = etag
                    yield StorageEvent(
                        bucket=self._bucket, key=key, size=obj["Size"], event_time=obj["LastModified"]
                    )
            await asyncio.sleep(poll_interval)

    async def read_object(self, key: str) -> str:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read().decode("utf-8")
