#!/usr/bin/env python
"""Shadow mode (doc 8.3): runs the pipeline end to end (steps 3-9) for
every log file under a directory, but never calls review/submit - nothing
reaches Jira or email. Prints each incident's diagnosis for a human to
compare against what the manual process concluded, per doc 8.3 "outputs
are compared against the manual process" and the doc 9 "Shadow comparison"
test-strategy row (which is inherently a human/parallel-run activity, not
something this script can grade automatically).

Usage:
    python scripts/run_shadow_mode.py path/to/sample_logs_dir
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geniebot.db.session import get_sessionmaker, init_models
from geniebot.ingestion.incident_factory import create_incident_if_failure
from geniebot.ingestion.s3_listener import StorageEvent
from geniebot.kb.factory import get_vector_store
from geniebot.llm.factory import get_llm_client
from geniebot.orchestration.pipeline import build_pipeline_context, process_incident_from_log


async def main(log_dir: Path) -> None:
    await init_models()
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        ctx = build_pipeline_context(get_llm_client(), get_vector_store(session))

        for path in sorted(log_dir.rglob("*")):
            if not path.is_file():
                continue
            raw_text = path.read_text(encoding="utf-8", errors="replace")
            event = StorageEvent(bucket="shadow", key=str(path.relative_to(log_dir)), size=path.stat().st_size, event_time=None)  # type: ignore[arg-type]

            incident = await create_incident_if_failure(session, event, raw_text, working_prefix="")
            if incident is None:
                print(f"{path.name}: not a failure log, skipped")
                continue

            await process_incident_from_log(session, incident.incident_id, raw_text, ctx)
            await session.refresh(incident)

            diag = incident.diagnosis or {}
            print(
                f"{path.name}: status={incident.status.value} "
                f"root_cause={diag.get('root_cause', '')[:80]!r} "
                f"confidence={diag.get('confidence')} "
                f"resolution_type={diag.get('resolution_type')}"
            )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(Path(sys.argv[1])))
