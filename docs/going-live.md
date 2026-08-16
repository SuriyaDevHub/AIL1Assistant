# Going live: real data and a trained knowledge base

This doc covers two things the README's [production cutover
checklist](../README.md#production-cutover-checklist) doesn't spell out in
step-by-step form: how to actually run this against real Genie Bot traffic
instead of the sample corpus, and how to build/maintain a real knowledge
base. It complements that checklist and [docs/runbook.md](runbook.md) -
read those too; this doc doesn't repeat what they already cover well
(the exact `*_BACKEND` flags, or day-2 incident-response scenarios).

## Part 1 — Pointing it at real data

### 1.1 Deployment shape changes first

`scripts/seed_and_run_worker.py` (what every earlier demo in this repo's
history used) is **dev-only**. It runs the API, the ingestion poller, and
the worker loop in one process specifically because `VECTOR_STORE_BACKEND=memory`
is a process-wide singleton - three separate processes would each get
their own empty index. Once `VECTOR_STORE_BACKEND=pgvector` (the real
default) and `DATABASE_URL` points at a real Postgres with pgvector,
that constraint disappears - Postgres is genuinely shared state. Run:

- The API: `uvicorn geniebot.main:app` (however many replicas you want,
  behind a load balancer).
- The worker: `python -m geniebot.worker_main` (`src/geniebot/worker_main.py`)
  - separately, scaled independently from the API. Its own docstring notes
  ingestion and processing can be split into separate deployments if they
  need to scale independently of each other too.

Run `alembic upgrade head` as an explicit deploy step before either starts
(`deploy/k8s/migrate-job.yaml`) - production intentionally does not
auto-create tables on startup the way the dev script does.

### 1.2 Real log ingestion

Two ways in, pick based on how Genie Bot (or whatever's producing the
failure logs) actually delivers them:

- **It writes to S3** (or a shared path): `STORAGE_BACKEND=s3` +
  `S3_BUCKET` / `S3_WORKING_PREFIX`. The polling listener
  (`ingestion/s3_listener.py::S3EventSource`) discovers new objects itself.
  Confirm the real key convention (`{bot_id}/{job_run_id}/execution.log`)
  and the `# user_id: / # environment:` header convention
  (`ingestion/incident_factory.py::_parse_identifiers`) actually match
  what's produced - adjust that one function if not, everything downstream
  is unaffected.
- **Something else already knows when a job failed** (an RPA platform, a
  job scheduler webhook): use `POST /ingest/trigger` instead
  (`{bucket, key, size}`, pulls the object itself rather than waiting to be
  polled - see `api/routers/ingest.py`). This is still a placeholder
  contract pending the real trigger payload/auth from whatever's calling
  it; swap those two things in once known, the rest of the pipeline is
  unaffected either way.

### 1.3 Real LLM backend

`LLM_BACKEND=internal_platform` + `INTERNAL_AI_PLATFORM_BASE_URL` (your
platform's FQDN) is the intended production path for a restricted network.
Auth is OpenAM/DSP (`OPENAM_TOKEN_URL`, `DSP_TRANSLATE_URL`,
`OPENAM_CLIENT_ID`, `OPENAM_CLIENT_SECRET`) - independent of `AUTH_BACKEND`,
which is the separate, unrelated setting for how end users log into the
review UI. If your platform's FQDN presents an internally-signed cert, set
`INTERNAL_AI_PLATFORM_CA_BUNDLE` to a PEM file path.

`LLM_BACKEND=anthropic` (real Anthropic API) or `openai` (OpenAI/Azure
OpenAI/any OpenAI-compatible endpoint) both work directly if a restricted
network isn't a constraint for your case.

`GENERATION_MODEL` is optional - leave empty to let each agent use the
model already pinned in its own prompt config
(`config/prompts/*.yaml`'s `model:` field, since a prompt is often tuned
against one specific model's response style). Set it to override every
agent to one model in one place instead, e.g. to point the whole pipeline
at whatever model name your internal platform serves.

Confirm the wire format actually matches
(`llm/internal_platform_client.py` assumes an OpenAI-compatible
`/chat/completions` + `/embeddings` surface) - adjust the two request/response
mappings in that one file if your platform's contract differs; nothing
else in the codebase needs to change either way.

### 1.4 Everything else in the checklist

Real Jira (`JIRA_BACKEND=real`, plus wiring your project's closure webhook
to `POST /incidents/webhooks/jira-closure` - this is what makes an
escalated incident actually close when L2 closes the real ticket), real
SMTP, real secrets manager, real OpenAM login for end users, CORS
restriction, and the moderation-service swap are all covered in the
[README checklist](../README.md#production-cutover-checklist) - follow it
for those.

One item worth calling out here: `config/taxonomy.yaml`'s `rollout_status`
(`pilot` / `expanded` / `disabled` per category) is your actual go-live
throttle, separate from all the backend flags above. Every category ships
`disabled` except `CONNECTIVITY` and `AUTH_EXPIRED` (`pilot`). A `disabled`
category always escalates regardless of confidence
(`agents/confidence_gate.py`) - move a category to `pilot` only once
you've seen it perform well in shadow/pilot traffic (`scripts/run_shadow_mode.py`,
doc section 11 phase 10), via `PUT /admin/config` (`file: "taxonomy"`) or
a direct file edit + redeploy.

Also worth curating for real before go-live: `config/generic_l1_checklist.yaml`
- the fallback steps shown to an end user when there's no KB precedent at
all. The shipped defaults are generic placeholders; replace them with your
own ops team's actual first-response steps per category.

### 1.5 Pausing safely at any time

`POST /admin/killswitch {"enabled": false}` routes every new incident
straight to `MANUAL_FALLBACK` without calling the LLM at all - the fastest
way to stop automated processing if something looks wrong, without a
redeploy. `GET /admin/killswitch` to check current state. See
[docs/runbook.md](runbook.md) for the specific scenarios this is meant for.

## Part 2 — Training the knowledge base

The sample corpus (`scripts/seed_kb_sample_corpus.py`) is 3 sample SOPs, 3
sample Jira tickets, and a handful of sample error/log entries - enough to
demonstrate retrieval, not enough to diagnose real incidents well. This is
the one piece of doc section 11's plan explicitly marked "sample corpus
only, real historical data not available" - it's on you to point it at
real content.

### 2.1 How a document becomes retrievable

Every document goes through the same pipeline
(`kb/build_pipeline.py::run_full_build`), regardless of source: extract →
normalise → **redact** (same guardrail engine as runtime) → **classify**
(`public`/`internal`/`confidential`/`restricted` - anything at or above a
configurable threshold is excluded, never silently indexed) → **dedupe**
(exact hash, then fuzzy similarity) → label with a taxonomy category →
chunk → embed → index into a named `index_version` → evaluate against a
golden set → promote (or roll back) based on whether recall/precision beat
the currently active index.

Nothing reaches retrieval without going through classification and
redaction first - a document tagged `restricted` gets excluded and shows
up in the build report's `excluded` list, not silently dropped.

### 2.2 Wiring the five extractors to real sources

Each extractor in `kb/sources/` (`JiraTicketExtractor`,
`MailboxArchiveExtractor`, `SOPExtractor`, `ErrorCatalogueExtractor`,
`LogSampleExtractor`) takes an optional `fetch` argument - an async
callable returning a list of dicts. Without one, each falls back to its
own hardcoded sample data. To go real, implement `fetch()` per source and
pass it in when building the extractor list:

| Extractor | doc_type | Real source | fetch() must return |
|---|---|---|---|
| `JiraTicketExtractor` | resolved_incident | Closed tickets from your real Jira project | `[{"key", "summary", "body"}, ...]` |
| `MailboxArchiveExtractor` | template | Past support-mailbox threads | `[{"id", "subject", "body"}, ...]` |
| `SOPExtractor` | sop | Your real, existing runbooks/SOPs | `[{"id", "title", "body"}, ...]` |
| `ErrorCatalogueExtractor` | runbook | A known-errors catalogue, if one exists | `[{"id", "exception_type", "root_cause", "category"}, ...]` |
| `LogSampleExtractor` | runbook | Annotated historical log samples | `[{"id", "category", "trace"}, ...]` |

`scripts/seed_kb_sample_corpus.py` is the wiring point - copy it, swap the
five `Extractor()` constructions for `Extractor(fetch=your_real_fetch)`,
keep the rest (it already calls `run_full_build` with a sensible
`index_version="v1-seed"`).

### 2.3 Building an initial index without an immediate go-live

`run_full_build` auto-promotes the new index immediately **unless** you
pass an `eval_set` (a list of `EvalCase`s - a query plus the
`source_ref`s expected back). With one, promotion is gated: the new index
only goes live if its recall and precision beat the currently active
index's (pass the active index's own `recall`/`precision` from
`IndexVersion` as `current_recall`/`current_precision`). Build a golden
eval set of realistic queries with known-correct expected sources early -
`scripts/evaluate_retrieval.py` and `scripts/evaluate_golden_set.py` are
the harnesses for this, and the README checklist already calls for
re-running them against real backends before cutover.

To promote or roll back a specific version manually later:
`POST /kb/index/promote {"index_version": "..."}`
(`api/routers/kb.py`) - also the lever for rolling back a bad promotion
per the runbook's "KB regression after promotion" scenario.

### 2.4 The knowledge base grows on its own after go-live - two mechanisms

**Every resolved incident feeds back automatically.** When a Jira ticket
closes (real webhook, or a self-resolved incident closing its own ticket
immediately), `feedback/resolution_capture.py::capture_resolution` builds
a `resolved_incident` document from that incident's diagnosis/template and
indexes it into the currently active index - no human action needed. A
rejected review is recorded as a negative signal instead, never indexed.

**Recurring patterns draft their own SOP, pending your approval.**
`feedback/sop_drafter.py::maybe_draft_sop` watches for the same error
signature closing `sop_min_occurrences` times (default 3,
`config/thresholds.yaml`) within `sop_window_hours` (default 720h/30 days)
and deterministically drafts a `SopDraft` (status `pending`, no LLM call -
templated straight from the resolved incidents' own content). It does
**not** index itself - review and act on it:

- `GET /kb/sop-drafts?status=pending` - see what's waiting.
- `POST /kb/sop-drafts/{draft_id}/approve` - indexes it as a real `sop`
  document (superseding any prior SOP for that same error signature) and
  marks the draft approved.
- `POST /kb/sop-drafts/{draft_id}/reject` - marks it rejected, never
  indexed.

This is the main recurring operational task once live: check pending SOP
drafts periodically and approve the ones that are actually correct -
don't rubber-stamp them, they're templated from raw incident data, not
reviewed for accuracy.

### 2.5 Adding a one-off document by hand

`POST /kb/documents` indexes a single pre-approved document into the
active index immediately (still goes through classification - a
`restricted`-tagged submission is rejected with 409/422, not silently
indexed). Useful for adding one new SOP without a full rebuild.

## Part 3 — Verifying it end to end

1. Confirm the killswitch is enabled (`GET /admin/killswitch`) and an
   index is active before expecting anything to work
   (`capture_resolution`/retrieval both no-op or degrade gracefully
   without one).
2. Drop or trigger one real log and follow it through
   `GET /incidents/{id}/timeline` - confirm it reaches `AWAITING_REVIEW`
   (or a fail-closed terminal state, if something's misconfigured) and
   check the audit ledger for exactly what happened at each step.
3. Resolve or escalate it through the real flow and confirm the real Jira
   ticket and email actually land.
4. Close that real ticket and confirm `GET /incidents/{id}` transitions to
   `CLOSED` and a `FEEDBACK_RESOLUTION_CAPTURED` audit event appears - the
   feedback loop working is the real go/no-go signal that the whole chain
   (webhook → capture → index) is wired correctly.
