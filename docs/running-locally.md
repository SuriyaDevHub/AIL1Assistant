# Running it locally

Assumes [docs/setup.md](setup.md) is done (venv activated, `pip install -e ".[dev]"`
run, `pytest` passing). This covers the actual day-to-day workflow: start
the app, get a token, drop a test log, watch it turn into an incident.

## The one command that starts everything

```bash
python scripts/seed_and_run_worker.py
```

This seeds the knowledge base, starts the ingestion listener, starts the
worker loop, and serves the API - all in **one process**, on
`http://127.0.0.1:8000` by default (override with `GENIEBOT_API_HOST` /
`GENIEBOT_API_PORT`).

**Use this one, not `uvicorn geniebot.main:app` + `python -m geniebot.worker_main`
as separate processes**, for local dev. The reason is specific to the
default `VECTOR_STORE_BACKEND=memory` (and equally to `VECTOR_STORE_BACKEND=faiss`):
both are single-process-scoped - running the API and worker as separate
processes locally means each gets its own empty knowledge-base index, so
retrieval silently never finds anything the other process indexed. This
constraint goes away once you're on `VECTOR_STORE_BACKEND=pgvector`
against a real Postgres (real, shared state) - see
[docs/going-live.md](going-live.md) for when and how to run the API and
worker as genuinely separate processes.

Stop it with Ctrl+C, or (if it's running in the background and you've
lost the terminal) find and kill the process listening on port 8000.
There's no `--reload` - restart it manually after any backend code
change.

**Every restart re-seeds the knowledge base and re-ingests everything
already sitting in `local_s3/working/`** (the polling listener has no
persistent "already seen" cursor across restarts). With
`VECTOR_STORE_BACKEND=memory` this also means every restart starts with
an empty incident history question - the database (SQLite,
`geniebot_local.db`) *does* persist across restarts, but the knowledge
base index doesn't unless you're on `faiss` or `pgvector`. If local
testing has left `local_s3/working/` cluttered with old test logs and
you want a genuinely clean slate:

```bash
rm -f geniebot_local.db && rm -rf local_s3/working
```

## Getting a token

The review UI's `TokenBar` (and any direct API call) needs a bearer
token. This reference implementation doesn't build the real OpenAM
login redirect - `AUTH_BACKEND=mock` (the default) issues locally-signed
dev tokens instead:

```python
from geniebot.api.deps import create_dev_token
print(create_dev_token("your-name", role="admin"))
```

`role` is `"end_user"` (default - only sees incidents they own, no
Dashboard), `"l2_support"`, or `"admin"` (both see everything and the
Dashboard). Tokens are signed per-process (`API_DEV_SHARED_SECRET`) -
**a token from a previous run of the app is invalid after a restart**;
generate a fresh one each time you restart the backend.

## Starting the review UI

In a separate terminal:

```bash
cd frontend
npm run dev
```

Opens on `http://localhost:5173` by default, proxying API calls to
`http://127.0.0.1:8000`. Paste a token into the "dev bearer token" field
in the header and click "Set token."

## Watching an incident flow through

Drop a file anywhere under `local_s3/working/<bot_id>/<job_run_id>/` -
the listener polls every 5 seconds by default. See
[docs/log-format.md](log-format.md) for exactly what the log needs to
contain; the short version:

```bash
mkdir -p local_s3/working/bot-payments-recon/run-001
cat > local_s3/working/bot-payments-recon/run-001/execution.log <<'EOF'
# user_id: asharma
# environment: production
ConnectionError: could not connect to host db01.internal
  at payments/loader.py:142 in load_batch
FATAL job failed
EOF
```

Then, with a token:

```bash
curl -s http://127.0.0.1:8000/incidents -H "Authorization: Bearer <token>"
```

The incident starts at `INGESTED` and moves through `SCREENED` →
`PARSED` → `DIAGNOSED` → (`AUTO_RESOLVE_CANDIDATE` or
`ESCALATION_DRAFTED`) → `AWAITING_REVIEW` as the pipeline runs -
`GET /incidents/{id}/timeline` shows the real, timestamped sequence.
With `LLM_BACKEND=mock` (the default) this takes well under a second per
step; against a real LLM backend, each step is a real model call and can
take several seconds - be patient rather than assuming something's stuck.

From there, in the UI: open the incident, and either confirm the
suggested fix worked ("Yes, it's fixed" - closes immediately, creates a
Jira ticket for the record) or escalate ("Still stuck? Escalate to L2" -
leaves the incident open until the ticket actually closes).
`POST /incidents/{id}/simulate-jira-closure` is the local stand-in for
"L2 closes the real ticket," since there's no real Jira connected by
default.

## Switching backends locally without going all the way to real
infrastructure

- `LLM_BACKEND=mock` (default, free, deterministic, no network) →
  `anthropic` / `openai` / `internal_platform` for a real model - see
  [docs/going-live.md](going-live.md) §1.3.
- `VECTOR_STORE_BACKEND=memory` (default) → `faiss` to keep the
  knowledge base across restarts without needing Postgres - see
  [docs/going-live.md](going-live.md) §1.1.
- `JIRA_BACKEND=mock` / `MAIL_BACKEND=mock` (defaults) - inspect what
  would have been created/sent via `GET /incidents/{id}/jira`, or by
  reading the `MockMailClient`/`MockJiraServer` state directly in a
  Python shell if you need to go deeper than the API exposes.

Every one of these is just an env var - no code change, no rebuild.

## Common local dev problems

- **Just restarted, everything 401s**: the token you had is signed by
  the *previous* process (see "Getting a token" above) - generate a new
  one.
- **A fresh log dropped in isn't showing up**: the listener polls every
  5 seconds - give it a moment. If it's genuinely stuck, check the
  process's own stdout/log output for exceptions during ingestion
  (`ingestion failed for key=...` is the log line to look for).
- **The same job_run_id keeps reappearing as a new incident after every
  restart**: expected - see "every restart re-seeds and re-ingests"
  above. Clear `local_s3/working/` if you don't want that.
- **A pipeline step seems to hang for a long time**: normal against a
  real LLM backend under load (see "Watching an incident flow through"
  above) - check `GET /incidents/{id}/timeline` to see it's actually
  progressing, not stuck.
