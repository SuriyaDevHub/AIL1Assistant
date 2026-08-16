# Setup

Getting the repo installed and verified. For actually running the app
day to day, see [docs/running-locally.md](running-locally.md). For real
production infrastructure instead of the local defaults, see
[docs/going-live.md](going-live.md).

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (frontend only - skip if you're only touching the backend/API)
- **Git**

Nothing else is required for local development - no Docker, no
Postgres, no external services. Every backend defaults to a local/mock
implementation (SQLite, an in-process queue, a local filesystem
standing in for S3, a deterministic mock LLM) so the whole stack runs
with zero external dependencies.

## 1. Clone and create a virtual environment

```bash
git clone https://github.com/SuriyaDevHub/AIL1Assistant.git
cd AIL1Assistant
python -m venv .venv
```

Activate it:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (Git Bash / this repo's own dev environment)
source .venv/Scripts/activate

# macOS / Linux
source .venv/bin/activate
```

## 2. Install the backend

```bash
pip install -e ".[dev]"
```

This installs the app itself plus dev tooling (pytest, ruff, mypy).
`faiss-cpu` and `numpy` are core dependencies as of the local-FAISS
vector store addition - if your network is restricted, confirm they
resolve from your internal PyPI mirror before this step (see
[docs/going-live.md](going-live.md), "Pre-flight").

## 3. Configure environment variables

Everything has a working local default - you don't strictly need a
`.env` file at all to get started. To customize anything (switch to a
real LLM backend, point at a different local port, etc.), copy the
example and edit it:

```bash
cp .env.example .env
```

`.env.example` documents every setting with its default and what a real
value looks like. `.env` is gitignored - never commit real credentials
into it or into any tracked file.

## 4. Verify the install

```bash
pytest
```

Should show `186 passed` (or more, as the suite grows) with no
failures. This runs entirely against mock backends (forced by
`tests/conftest.py` regardless of what's in your `.env`) - no network
calls, no external services, so a clean run here confirms the install
itself is correct before you touch anything live.

## 5. Install the frontend (optional, only if working on the review UI)

```bash
cd frontend
npm install
```

Verify it builds:

```bash
npx tsc -b && npm run build
```

## Next

- [docs/running-locally.md](running-locally.md) - actually running the
  app and watching an incident flow through it.
- [docs/log-format.md](log-format.md) - what a Genie Bot execution log
  needs to look like for the ingestion pipeline to pick it up correctly.

## Common setup problems

- **`pip install -e ".[dev]"` fails on `faiss-cpu` or `asyncpg`**: both
  are compiled/binary packages. Confirm your Python version has a
  prebuilt wheel available (check `pip index versions faiss-cpu` against
  your Python version) - a restricted-network PyPI mirror not yet
  mirroring a wheel for your exact Python/OS/arch combination is the
  most common cause.
- **`pytest` fails with a real network error** (rate limit, DNS
  failure): your shell's own environment variables are taking
  precedence over `tests/conftest.py`'s hermetic-test forcing for
  something it doesn't already force - check what env vars are actually
  set (`env | grep -i backend`) and compare against the four it does
  force (`LLM_BACKEND`, `AUTH_BACKEND`, `JIRA_BACKEND`, `MAIL_BACKEND`,
  `VECTOR_STORE_BACKEND`).
- **`ModuleNotFoundError: No module named 'geniebot'`**: the venv isn't
  activated, or `pip install -e .` wasn't run inside it - check
  `which python` (or `Get-Command python` in PowerShell) points at
  `.venv`, not a system Python.
