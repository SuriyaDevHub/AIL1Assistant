# GenieBot L1 Assistant

Event-driven agentic pipeline that triages Genie Bot batch-automation
failures: an S3 log write triggers guardrailed parsing and diagnosis
against a knowledge base, a confidence gate decides between an
auto-resolve candidate and an L2 escalation, a human reviewer approves or
rejects, and approved incidents create a Jira ticket and notify the
support mailbox. Closed tickets feed back into the knowledge base.

Full design reference: [docs/GenieBot_L1_Technical_Design_Development.md](docs/GenieBot_L1_Technical_Design_Development.md)

## What's real vs. what's mocked

This is a complete, working implementation of every layer in the design
doc - business logic, data model, state machine, guardrails, the four
agents, the KB build/retrieval pipeline, dedup, the audit ledger, the
REST API, the review UI, and tests across every category in doc section 9.
It runs end to end today with zero external dependencies.

What it is **not**: connected to the real bank infrastructure the design
doc targets (an internal GPT-5.1 platform, OpenAM/DSP, live Jira,
production S3, the approved secrets manager). I have no credentials or
network access to any of those. Every one of them sits behind an
interface with two implementations - a mock/local one (default, used
below) and a real one written against the documented API shape. Switching
is a config change, not a code change; see
[Production cutover checklist](#production-cutover-checklist).

| Concern | Interface | Mock/local (default) | Real |
|---|---|---|---|
| Generation + embedding | `llm.client.LLMClient` | `MockLLMClient` - deterministic, rule-based | `InternalPlatformLLMClient` |
| Outbound auth | `llm.auth.TokenProvider` | `MockTokenProvider` | `OpenAMDSPTokenProvider` |
| API auth | JWT bearer | locally-signed dev token | OpenAM-issued JWT, JWKS-verified |
| Vector store | `kb.vector_store.VectorStore` | `InMemoryVectorStore` | `PgVectorStore` (pgvector) |
| Object storage | `ingestion.s3_listener.StorageEventSource` | `LocalFilesystemEventSource` | `S3EventSource` |
| Queue | `queue.base.Queue` | `MemoryQueue` | `SQSQueue` |
| Ticketing | `integrations.jira_client.JiraClient` | `MockJiraServer` | `RealJiraClient` |
| Mail | `integrations.mail_client.MailClient` | `MockMailClient` | `RealSMTPClient` (works with MailHog too) |

## Quickstart

### Python backend, no Docker

```bash
python -m venv .venv
source .venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
pytest                          # 119 tests across doc section 9's categories
```

Everything defaults to the mock/local backends above, so this runs with a
SQLite file and no other services. To run the API:

```bash
uvicorn geniebot.main:app --reload
```

`GET /health` should return `{"status": "ok", ...}`. To exercise the full
pipeline, seed a sample knowledge base and drop a failing log into the
local fake-S3 directory:

```bash
python scripts/seed_kb_sample_corpus.py
python -m geniebot.worker_main     # in another terminal - ingestion + processing loop
```

then write a file under `./local_s3/working/<bot_id>/<job_run_id>/execution.log`
containing a recognisable exception (see `ingestion/incident_factory.py`
for the log-header/key convention) and watch it become an incident via
`GET /incidents`.

### Review UI

```bash
cd frontend && npm install && npm run dev
```

Auth is a pasted dev bearer token (see the `TokenBar` in the header) since
this reference implementation doesn't build the real OpenAM login redirect
- generate one locally with:

```python
from geniebot.api.deps import create_dev_token
print(create_dev_token("your-name"))
```

## Repository layout

```
config/                 thresholds, guardrail rules, taxonomy, prompts, template schema - all versioned, hot-reloadable via /admin/config
src/geniebot/
  db/                    SQLAlchemy models, Alembic migrations, state machine
  schemas/               pydantic contracts (agent I/O, API, KB)
  guardrails/            input + output guardrails (doc 6)
  llm/                   LLMClient abstraction, mock + real + auth
  agents/                base agent contract + Log Parser / Diagnostic / Confidence Gate / Template Generator (doc 5)
  kb/                    vector store, build pipeline, chunking, retrieval, source extractors (doc 4)
  ingestion/              S3 event source, incident creation (doc 2.2 steps 1-2)
  queue/                  durable queue abstraction + memory/SQS backends
  orchestration/          pipeline (doc 2.2 steps 3-9), worker loops
  integrations/           Jira, mail, dedup, Integration Agent (doc 5.6, 7.3)
  feedback/               resolution capture, SOP drafting, re-indexing (doc 4.5)
  api/                    FastAPI routers matching doc 7.1's endpoint table
  observability/          structured logging, Prometheus metrics
  security/killswitch.py  doc 7.1 /admin/killswitch
frontend/                React review UI
tests/                   one directory per doc section 9 test category
scripts/                 seed_kb_sample_corpus, evaluate_golden_set, evaluate_retrieval, run_shadow_mode
deploy/k8s/              Deployment/Service/ConfigMap/Secret/HPA templates + migration Job
docs/                    design doc, runbook, risks, open items
```

## Configuration

Everything is env-driven (see [.env.example](.env.example)) plus
`config/*.yaml` for thresholds, guardrail rules, and taxonomy (doc 8.2:
"externalised configuration, versioned in source control"). `GET/PUT
/admin/config` reads/writes those YAML files at runtime for
dev/UAT calibration; production config changes should go through source
control review instead (see the docstring in `api/routers/admin.py`).

## Production cutover checklist

For each row, flip the `*_BACKEND` env var and supply the matching
credentials/endpoint - no code changes required:

- [ ] `LLM_BACKEND=internal_platform` + `INTERNAL_AI_PLATFORM_BASE_URL` - confirm the actual chat/embeddings wire format matches `llm/internal_platform_client.py`'s OpenAI-compatible assumption; adjust the two request/response mappings if not.
- [ ] `AUTH_BACKEND=openam` + `OPENAM_TOKEN_URL` / `DSP_TRANSLATE_URL` / client credentials + `JWT_JWKS_URL` for inbound API verification.
- [ ] `VECTOR_STORE_BACKEND=pgvector` (already the default) + real `DATABASE_URL` with the pgvector extension available.
- [ ] `STORAGE_BACKEND=s3` + `S3_BUCKET` - confirm the real Genie Bot log key/header convention matches `ingestion/incident_factory.py`, adjust if not.
- [ ] `QUEUE_BACKEND=sqs` + `SQS_QUEUE_URL` / `SQS_DLQ_URL` with a redrive policy configured at the infra level.
- [ ] `JIRA_BACKEND=real` + `JIRA_BASE_URL` / `JIRA_API_TOKEN` / `JIRA_USER_EMAIL`; configure the real Jira project's closure webhook to POST to `/incidents/webhooks/jira-closure`.
- [ ] `MAIL_BACKEND=smtp` + real `SMTP_HOST`.
- [ ] `SECRETS_BACKEND=approved_secrets_manager` - none of the above credentials should live in `.env` in production; wire them from the approved secrets manager (k8s `ExternalSecret` or equivalent, see `deploy/k8s/secret.example.yaml`).
- [ ] Run `alembic upgrade head` as an explicit deploy step (`deploy/k8s/migrate-job.yaml`) - production intentionally does not auto-create tables on startup.
- [ ] Swap the moderation stub in `guardrails/output_guardrails.py::moderation_flagged` for the approved content-moderation service.
- [ ] Re-run `scripts/evaluate_golden_set.py` and `scripts/evaluate_retrieval.py` against the real backends and set `config/thresholds.yaml`'s confidence/retrieval/budget values from the results - the shipped values are placeholders (see [docs/open_items.md](docs/open_items.md)).
- [ ] Confirm `config/thresholds.yaml`'s `cost_per_1k_tokens_usd` against the internal platform's actual pricing.
- [ ] Restrict `main.py`'s CORS `allow_origins` to the real Review UI origin(s).
- [ ] Build the real OpenAM login flow into the frontend, replacing the dev `TokenBar`.

## Development plan status (doc section 11)

| Phase | Workstream | Status |
|---|---|---|
| 0 | Scoping | Taxonomy/thresholds seeded with placeholders - needs real agreement, see [docs/open_items.md](docs/open_items.md) |
| 1 | Foundation | Done - ingestion, incident model, queue, audit ledger |
| 2 | Knowledge base | Done - build pipeline, chunking, retrieval, evaluation harness; sample corpus only, real historical data not available |
| 3 | Guardrails | Done - input + output guardrails, fail-closed routing, adversarial + guardrail-negative tests |
| 4 | Agents | Done - all four agents, schema-valid on the sample corpus |
| 5 | Human-in-the-loop | Done - review API + UI, approve/reject/rerun |
| 6 | Integration | Done - Jira creation, mail dispatch, dedup |
| 7 | Feedback loop | Done - resolution capture, SOP drafting + approval, re-indexing |
| 8 | Observability | Done - structured logs, Prometheus metrics, audit ledger |
| 9 | Governance | Not started - security/privacy/model-risk review is a human process this repo can't stand in for |
| 10 | Shadow & pilot | `scripts/run_shadow_mode.py` gives the mechanics; the actual parallel run and calibration is an operational activity against real traffic |
| 11 | Rollout | k8s templates + kill-switch + runbook in place; category-by-category rollout is controlled via `config/taxonomy.yaml`'s `rollout_status` field |

See also: [docs/runbook.md](docs/runbook.md), [docs/risks.md](docs/risks.md), [docs/open_items.md](docs/open_items.md).
