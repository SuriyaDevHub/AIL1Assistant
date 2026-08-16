# Open Items

Restates the design doc's open items (section 13) plus every placeholder
value introduced while building this reference implementation, so a real
team can find and replace them in one pass rather than discovering them
one at a time in production.

## From the design doc (section 13)

- **Latency and availability targets** - not confirmed with production
  support. `config/thresholds.yaml`'s `latency` block has provisional
  `target_p50_seconds`/`target_p99_seconds` values so alerting has
  something to compare against; treat them as placeholders.
- **Confidence threshold and top-N retrieval values** - `config/thresholds.yaml`'s
  `confidence.auto_resolve_min` (0.80) and `retrieval.top_n` (5) are
  reasonable starting points, not calibrated against real data. Run
  `scripts/evaluate_golden_set.py` and `scripts/evaluate_retrieval.py`
  against real incidents/backends and adjust.
- **Vector store selection** - implemented against pgvector (the doc's
  named fallback option) rather than an "approved internal vector
  database", since no such platform was specified or accessible. The
  `VectorStore` abstraction (`kb/vector_store.py`) makes swapping in a
  different backend a matter of adding one more implementation.
- **Error taxonomy** - `config/taxonomy.yaml` is seeded with a
  representative starting set (connectivity, auth expiry, data validation,
  resource exhaustion, dependency unavailable, scheduling conflict, config
  error, unknown) covering common batch-automation failure classes, with
  only `CONNECTIVITY` and `AUTH_EXPIRED` set to `rollout_status: pilot`
  (everything else `disabled`, per doc 1.4's "narrowest, lowest-risk
  category first" release approach). Needs real agreement before KB
  labelling begins against real historical data.
- **Diagnosis accuracy / false-resolution rate acceptance thresholds** -
  not set anywhere, since there's no agreed number. `scripts/evaluate_golden_set.py`
  measures accuracy against a small synthetic fixture set
  (`tests/golden_set/fixtures/golden_incidents.json`) as a mechanism, not
  a substitute for the real acceptance threshold being agreed at Phase 0.

## Introduced by this implementation (not doc-specified)

- **`cost_per_1k_tokens_usd` (thresholds.yaml)** - a placeholder blended
  rate; the doc doesn't specify internal-platform pricing and explicitly
  flags cost estimation as unresolved (risk table, section 12).
- **Log key/header convention (`ingestion/incident_factory.py`)** - the
  doc says bot_id/job_run_id come "from the log path and header" but
  doesn't specify the exact format. Implemented as
  `{prefix}{bot_id}/{job_run_id}/execution.log` with an optional
  `# key: value` header block; confirm against the real Genie Bot
  log-writing convention.
- **Internal AI platform wire format (`llm/internal_platform_client.py`)** -
  assumes an OpenAI-compatible `/chat/completions` and `/embeddings`
  surface. Confirm against the platform's actual API contract.
- **`EMBED_DIM = 64` (`llm/client.py`)** - a placeholder embedding
  dimension for the mock client and the pgvector column size; must match
  whatever `text-embedding-small` actually returns before cutover.
- **SOP recurrence threshold (`thresholds.yaml`'s `feedback` block)** -
  `sop_min_occurrences: 3` / `sop_window_hours: 720` (30 days) is a
  reasonable-sounding default for "recurring pattern", not derived from
  any real incident volume data.
- **Moderation stub** - see [docs/risks.md](risks.md); not a real
  moderation service.
