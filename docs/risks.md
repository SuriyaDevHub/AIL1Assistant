# Risks & Mitigations

Restates the design doc's risk table (section 12) against what's actually
implemented here, so it's clear which mitigations exist in code today vs.
which are process/governance activities outside this repo's scope.

| Risk | Mitigation in this codebase | Still needed |
|---|---|---|
| Plausible but wrong diagnosis | Grounding check (`guardrails/output_guardrails.py::grounding_check`), Confidence Gate (`agents/confidence_gate.py`), mandatory human review (state machine forces `AWAITING_REVIEW` on every path), golden-set regression test | Real calibration of `confidence.auto_resolve_min` against actual model behaviour on real incidents - the shipped value is a placeholder |
| Sensitive data reaching the model | Pre-model redaction (`guardrails/redaction_patterns.py`), classification block (`guardrails/input_guardrails.py`), fail-closed on screening error, egress re-screening on generated output | The redaction ruleset is pattern-based; entity-based (NER) scrubbing via the approved data-classification service is not wired in - see `redaction_patterns.py`'s module docstring |
| Prompt injection via log content | Injection screening + neutralisation (`guardrails/input_guardrails.py::screen_injection`), typed schema outputs (every agent output is pydantic-validated, not free text), proposal-only enforcement on output | Adversarial test coverage (`tests/adversarial/`) is a fixed set of known patterns - expand it as new injection techniques are observed |
| Knowledge base decay | Supersession via `KBChunk.retired_at` (excluded from retrieval, not deleted), retrieval evaluation gate before every index promotion (`kb/build_pipeline.py::promote_or_rollback`), drift metric (`genie_retrieval_no_precedent_total`) | Real production traffic to actually observe decay against - the sample corpus is synthetic |
| Runaway cost from rerun loops | Per-incident budget ceiling + loop cap, both enforced as output guardrails and checked on every diagnosis (`guardrails/output_guardrails.py`) | `cost_per_1k_tokens_usd` is a placeholder rate (doc section 13 flags this as unresolved) - replace with the internal platform's real pricing before trusting cost telemetry |
| Over-reliance on the assistant | Confidence and citations surfaced prominently in the Review UI (`frontend/src/pages/IncidentDetail.tsx`) | A sampling audit process for approved incidents is an operational activity, not something this repo can automate |
| Production prompts larger than ideation | Token counts recorded per agent invocation (`AgentInvocationRecord`), Prometheus cost/token metrics per agent | Cost estimate needs revisiting after a real pilot, as the doc itself notes |

## Additional risks specific to this implementation

- **Mock-to-real transition risk**: the wire-format assumptions in
  `llm/internal_platform_client.py` (OpenAI-compatible chat/embeddings) and
  `ingestion/incident_factory.py` (log key/header convention) are
  reasonable defaults, not confirmed against the real systems. Verify both
  against real documentation/access before cutover.
- **In-memory backends don't survive process restarts**: `MemoryQueue` and
  `InMemoryVectorStore` are explicitly local/dev conveniences. Using them
  in anything resembling production would silently lose in-flight work on
  a crash or deploy. `QUEUE_BACKEND` defaults to `memory` even when
  `VECTOR_STORE_BACKEND=pgvector` is set against a real Postgres - flip
  to `sqs` before anything beyond local demoing.
- **Single moderation stub**: `guardrails/output_guardrails.py::moderation_flagged`
  is a minimal keyword blocklist, explicitly not a real content-moderation
  service. Treat the "Moderation" guardrail as unimplemented for
  production purposes until it's swapped.
