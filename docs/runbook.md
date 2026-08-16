# Runbook

Operational scenarios from the design doc (section 10.3), made concrete
against this codebase's actual commands/endpoints.

## Systematic misdiagnosis

**Symptom**: reviewer rejection rate climbing, or a sampling audit finds
approved incidents with an incorrect root cause.

1. Activate the kill-switch so no further incidents auto-progress past
   ingestion:
   ```bash
   curl -X POST $API/admin/killswitch -H "Authorization: Bearer $TOKEN" \
     -d '{"enabled": false}'
   ```
   New incidents route straight to `PLATFORM_UNAVAILABLE` ->
   `MANUAL_FALLBACK` (`security/killswitch.py`, checked first thing in
   `orchestration/pipeline.py::process_incident_from_log`).
2. Roll back the prompt version: edit `config/prompts/<agent>.vN.yaml`
   back to the prior version, or `PUT /admin/config`. The prompt version
   used is recorded on every incident (`Incident.active_prompt_versions`)
   so you can see exactly what changed and when.
3. Alternatively/additionally roll back the KB index:
   ```bash
   curl -X POST $API/kb/index/promote -H "Authorization: Bearer $TOKEN" \
     -d '{"index_version": "<prior_version>"}'
   ```
4. Re-run `python scripts/evaluate_golden_set.py` and
   `python scripts/evaluate_retrieval.py` before re-enabling.
5. Re-enable: `POST /admin/killswitch {"enabled": true}`.

## Duplicate ticket flooding

**Symptom**: many Jira tickets for what should be one incident.

1. Disable Integration Agent submission by turning the kill-switch off
   (this also stops new diagnoses, which is broader than strictly
   necessary but is the only global switch this codebase exposes -
   consider adding a submission-only flag if this scenario recurs).
2. Review `config/thresholds.yaml`'s `dedup` block
   (`exact_match_window_hours`, `fuzzy_similarity_threshold`) - a window
   too short or a fuzzy threshold too strict will under-match.
3. Every dedup decision is audited (`AuditRecord` rows with
   `event_type=DEDUP_DECISION`, and `integrations/dedup.py`) - query those
   to see which incidents should have linked but didn't, and why
   (`basis` is `null` when no match was found).
4. Manually link/close duplicate Jira tickets, then adjust thresholds and
   re-enable.

## Guardrail failure or bypass

**Symptom**: an incident reached a human (or was auto-submitted) with
ungrounded claims, a proposal-only violation, or unredacted sensitive data.

Treat as a security incident:

1. Kill-switch off immediately.
2. Every guardrail evaluation is audited
   (`event_type=GUARDRAIL_EVALUATION`, `rule_id`, `passed`, `disposition`)
   and exported as Prometheus counters
   (`genie_guardrail_evaluations_total{rule_id, passed}`) - pull the
   specific incident's audit trail and the rule's recent trigger-rate
   trend to find where the guardrail should have fired.
3. Audit every incident processed since the last known-good deploy for the
   same failure mode - the redaction rules (`guardrails/redaction_patterns.py`)
   and proposal-only patterns (`guardrails/output_guardrails.py`) are
   pattern-based and can have gaps; add a regression test under
   `tests/adversarial/` or `tests/guardrail_negative/` for the specific
   input that slipped through before re-enabling.

## AI platform unavailable

**Symptom**: `LLMPlatformError` rate climbing.

This is already handled automatically - every agent call failure of this
kind routes the incident to `PLATFORM_UNAVAILABLE` -> `MANUAL_FALLBACK`
(`orchestration/pipeline.py`), and the queue message is nacked for
redelivery up to its `max_attempts` before landing in the DLQ
(`queue/memory_queue.py` / `queue/sqs_queue.py`). Confirm no incidents were
lost: `dlq_messages()` (SQS: check the configured DLQ; memory backend: the
queue only lives as long as the worker process, so a crash mid-outage can
lose in-flight messages - this is a known gap of the in-memory backend,
another reason `QUEUE_BACKEND=sqs` is the production default).

## KB regression after promotion

**Symptom**: retrieval quality drops after an index rebuild.

`kb/build_pipeline.py::run_full_build` already gates promotion on
`evaluate_index` meeting or exceeding the current index's recall/precision
(doc 4.2 steps 11-12) - a regression should mean the candidate index was
never promoted in the first place. If it was (e.g. `current_recall`/
`current_precision` were passed incorrectly), roll back:

```bash
curl -X POST $API/kb/index/promote -H "Authorization: Bearer $TOKEN" \
  -d '{"index_version": "<prior_version>"}'
```

then investigate why the gate didn't catch it - check
`scripts/evaluate_retrieval.py`'s output against the eval set in
`tests/retrieval_eval/fixtures/eval_set.json` (expand it if the regression
wasn't covered).
