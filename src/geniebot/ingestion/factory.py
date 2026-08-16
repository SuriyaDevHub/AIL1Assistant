"""Factory for the storage backend's event source - same lru_cache-singleton
pattern as queue/factory.py's get_queue() and llm/factory.py's
get_llm_client(), so every consumer within a process (the polling ingestion
loop, a push-triggered endpoint like api/routers/ingest.py) shares one
instance rather than each holding its own independent view of "what's
already been seen"."""
from __future__ import annotations

from functools import lru_cache

from geniebot.ingestion.s3_listener import StorageEventSource
from geniebot.settings import get_settings


@lru_cache
def get_event_source() -> StorageEventSource:
    settings = get_settings()
    if settings.storage_backend == "s3":
        from geniebot.ingestion.s3_listener import S3EventSource

        return S3EventSource(bucket=settings.s3_bucket, prefix=settings.s3_working_prefix)

    from geniebot.ingestion.s3_listener import LocalFilesystemEventSource

    return LocalFilesystemEventSource(
        root=settings.local_s3_root, bucket_name=settings.s3_bucket, prefix=settings.s3_working_prefix
    )
