# Log format

What a Genie Bot execution log actually needs to contain for the
ingestion pipeline to pick it up and diagnose it correctly. Two layers
matter here, and they're enforced differently: the **ingestion**
contract (deterministic regex, `ingestion/incident_factory.py`) decides
whether a log becomes an incident at all and what bot/job/user it's
attributed to; the **parsing** contract (an LLM call,
`agents/log_parser_agent.py`) decides what root cause/evidence come out
of it, and is deliberately format-tolerant rather than regex-strict.

## 1. Where the file goes

```
{S3_WORKING_PREFIX}{bot_id}/{job_run_id}/<any filename>
```

Locally (`STORAGE_BACKEND=local`, the default) that's
`local_s3/working/{bot_id}/{job_run_id}/<any filename>` - the listener
watches every file under that tree recursively
(`ingestion/s3_listener.py::LocalFilesystemEventSource`), not
specifically a file named `execution.log` - that name is convention in
this repo's own examples, not an enforced requirement.

`bot_id` and `job_run_id` are read from these two path segments by
default. They can be overridden by a header line instead (below) if the
real log-writing convention doesn't put them in the path.

## 2. The optional header block

Any line at the very start of the file matching `# key: value` is
parsed as a header (`ingestion/incident_factory.py`'s
`_HEADER_LINE = re.compile(r"^#\s*(\w+):\s*(.+)$", re.MULTILINE)` -
matches anywhere in the file, not just the first lines, but put them at
the top for clarity). Four keys are recognized:

| Header | Default if absent | Notes |
|---|---|---|
| `# user_id: ...` | `"unknown"` | Who the incident is attributed to - this is what end-user visibility scoping (`GET /incidents`) filters on, so a real value matters once role-based access is in use. |
| `# environment: ...` | `"production"` | Must be one of `production`, `uat`, `development` (case-insensitive) - anything else silently falls back to `production`. |
| `# bot_id: ...` | the log's path segment | Only needed if the real convention doesn't put the bot id in the path. |
| `# job_run_id: ...` | the log's path segment | Same, for the job run id. |

Any other `# key: value` line is harmless - parsed into the header dict
and simply unused.

## 3. What makes a log a *failure* log

`is_failure_log` (`ingestion/incident_factory.py`) does a case-insensitive
search for any of: `error`, `exception`, `traceback`, `fatal`, or the
whole word `failed`, anywhere in the file. A log with none of these is
read but never turned into an incident - this is deliberate (doc 2.2
step 2: "failure logs create an incident record... success logs are
not"), not a bug if a genuinely-successful run's log goes unprocessed.

## 4. What the parser actually needs - and what it doesn't

The rest of the file is free text handed to the Log Parser Agent, which
does the real extraction (exception type, message, failing module, root
frame, evidence lines). Two things worth being precise about:

- **You do not need to number the lines yourself.** The pipeline numbers
  every line (`1: `, `2: `, ...) internally
  (`agents/log_parser_agent.py::_numbered`) before it reaches the model
  - write a plain, unnumbered log exactly as your system already
    produces it.
- **The format doesn't have to match any one convention.** With a real
  LLM backend (`LLM_BACKEND=anthropic`/`openai`/`internal_platform`),
  the parser reads the log the way a person would - a Python-style
  traceback, a `.NET`/C# unhandled-exception stack trace, or a
  structured JSON log line all work without any code change, since it's
  a genuine model call, not a fixed regex. This was verified directly
  this session: a real C# RPA-style trace (`Unhandled Exception:
  System.Security.Authentication.AuthenticationException: ...` with
  `at Namespace.Class.Method() in C:\path\File.cs:line N` frames) parsed
  correctly with zero pipeline changes.

  With `LLM_BACKEND=mock` (the default, deterministic, no network) the
  parser is a regex instead
  (`llm/mock_client.py::_ERROR_LINE = re.compile(r"^\s*(?P<lineno>\d+):\s*(?P<text>.*\b(?P<exc>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Timeout|Fault|Failure))\b.*)$")`)
  looking for a numbered line containing a token ending in `Error`,
  `Exception`, `Timeout`, `Fault`, or `Failure` - this matches both
  `ConnectionError` and `System.Security.Authentication.AuthenticationException`
  equally well, since it only cares about the suffix. Module extraction
  in mock mode (`module_match = re.search(r"in (?:module )?([\w./]+\.\w+)", ...)`)
  looks for `in <path>.<ext>` on that same line - works for
  `in module payments/loader.py` but won't reliably extract a Windows
  file path with backslashes from a real .NET trace; falls back to
  `"unknown"` rather than failing. This only affects mock-mode testing
  precision, not real-LLM-mode, which extracts correctly either way.

## 5. Example: minimal valid log

```
# user_id: asharma
# environment: production
INFO starting nightly payments reconciliation batch
ConnectionError: could not connect to host db01.internal
  at payments/loader.py:142 in load_batch
  at payments/loader.py:88 in run
  at scheduler/runner.py:41 in execute_job
FATAL job failed
```

Dropped at `local_s3/working/bot-payments-recon/run-001/execution.log`,
this produces `bot_id=bot-payments-recon`, `job_run_id=run-001`,
`user_id=asharma`, `environment=production`, and (real LLM backend)
extracts `exception_type=ConnectionError`,
`failing_module=payments/loader.py`, with each traceback line captured
as an evidence line.

## 6. Example: a non-Python format (proves the parser isn't Python-specific)

```
# source: C3.NET RPA Platform
# user_id: svc-collections
# environment: production
[INFO] C3.NET Robot Execution - WorkflowId: WF-COLLECT-0417
Unhandled Exception: System.Net.Http.HttpRequestException: Connection refused (settlements.internal:443)
   at System.Net.Http.HttpClient.SendAsync(HttpRequestMessage request, HttpCompletionOption completionOption, CancellationToken cancellationToken)
   at RpaBots.Collections.Services.SettlementClient.FetchBatchAsync(String batchId) in C:\Builds\C3\Bots\Collections\Services\SettlementClient.cs:line 84
[FATAL] Workflow terminated with unhandled exception - job aborted
```

No pipeline change needed for this to parse correctly under a real LLM
backend - `# source:` is just an unused header, harmless.

## 7. Testing a new/unfamiliar real format

Before wiring a new log-producing system into ingestion for real:

1. Drop one real (or realistic) sample under `local_s3/working/<bot>/<run>/`
   locally, against `LLM_BACKEND=anthropic`/`openai`/`internal_platform`
   (mock mode's regex is a much rougher approximation, not representative).
2. Watch it through `GET /incidents/{id}` - check `parse_output` for
   sensible `exception_type`/`failing_module`/`evidence_lines`, not just
   that it reached `AWAITING_REVIEW`.
3. If extraction looks wrong or incomplete, the fix is prompt wording in
   `config/prompts/log_parser.v1.yaml`, not new ingestion code - the
   ingestion layer (this doc, sections 1-3) only decides *whether* a log
   becomes an incident and who it's attributed to; everything about
   *what's in it* is the parser's job.
