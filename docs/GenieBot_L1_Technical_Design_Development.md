**GenieBot L1 Assistant**

*End-to-End Technical Design & Development Document*

Prepared by: Suriya \| SME Manager, Business & Data Analytics, CIB
Middle Office \| TCFC OMRC AI/ML

**1. Introduction**

**1.1 Purpose**

This document is the technical build reference for the GenieBot L1
Assistant. It translates the approved architecture and requirements into
implementable design: component structure, data models, knowledge base
construction, agent orchestration, prompt contracts, API specifications,
guardrail implementation, deployment topology and test strategy. It is
intended for the development team, reviewers and the model risk
assessor.

**1.2 Problem Statement**

Genie Bot batch automation failures are currently triaged manually. The
end user encounters an error, manually fills a support template and
emails the Genie Support Mailbox. Production support then reads the
email, manually searches execution logs in S3 and the working directory,
and manually creates a Jira ticket. This produces delayed triage,
inconsistent template quality, duplicate tickets and no reusable
knowledge from resolved incidents.

**1.3 Solution Summary**

An event-driven agentic pipeline replaces the manual path. Log write
events trigger automated parsing, retrieval-augmented diagnosis,
escalation template pre-fill and integration with Jira and email ---
with a mandatory human review gate before submission, guardrails on both
input and output, and a feedback loop that converts resolved incidents
into reusable knowledge base content.

**1.4 Design Principles**

- Proposal-only: the assistant recommends; it never executes remediation
  against production systems.

- Grounded output: every factual claim traces to parsed log evidence or
  a retrieved knowledge base entry.

- Fail closed: any guardrail, parsing or model failure routes the
  incident to the existing manual path rather than proceeding.

- Human gate first: automation of the submission step is earned through
  pilot evidence, not assumed at go-live.

- Everything auditable: prompts, retrieved context, outputs and human
  decisions are recorded per incident.

**2. Solution Architecture**

**2.1 Layered View**

  ----------- ---------------------- ----------------------------------------
  **Layer**   **Component**          **Responsibility**

  L1          Ingestion & Trigger    S3 event detection, failure
                                     classification, incident record
                                     creation, queueing

  L2          Input Guardrails       Redaction, data classification,
                                     injection screening, payload capping

  L3          Agent Pipeline         Log Parser, Diagnostic (L1 Triage),
                                     Confidence Gate, Template Generator

  L3          Knowledge Base         Vector store of incidents, SOPs and
                                     templates; semantic retrieval

  L4          Output Guardrails      Grounding check, proposal-only
                                     enforcement, moderation, budget and loop
                                     caps

  L5          Human-in-the-Loop      Review UI, edit, approve, reject,
                                     approve controlled rerun

  L6          Integration Agent      Jira ticket creation, email dispatch,
                                     deduplication

  L7          Feedback Loop          Resolution capture, SOP drafting, KB
                                     re-indexing

  L8          Observability          Cost, latency, quality metrics, drift,
                                     immutable audit

  L9          Resilience             Kill-switch, versioned configs, fallback
                                     routing, runbook
  ----------- ---------------------- ----------------------------------------

**2.2 End-to-End Sequence**

> 1\. Genie Bot writes execution log to S3 working directory.
>
> 2\. S3 event notification triggers the ingestion service; failure logs
> create an incident record and enqueue it.
>
> 3\. Input guardrails redact, classify and screen the log payload.
> Failure here routes to manual L2.
>
> 4\. Log Parser Agent extracts a structured error signature with
> line-level evidence references.
>
> 5\. Retrieval service embeds the error signature and queries the
> vector store for top-N similar historical incidents and SOPs.
>
> 6\. Diagnostic Agent produces root cause, proposed L1 resolution,
> cited evidence and a confidence score.
>
> 7\. Confidence Gate: at or above threshold continue; below threshold
> force escalation path.
>
> 8\. Template Generator Agent pre-fills the L2 escalation template
> where escalation is required.
>
> 9\. Output guardrails verify grounding, enforce proposal-only, run
> moderation and check budget and loop caps.
>
> 10\. Review UI presents diagnosis, evidence, confidence and template
> to production support.
>
> 11\. Reviewer edits, adds comments and approves, rejects, or approves
> a controlled rerun (which returns to step 6).
>
> 12\. Integration Agent deduplicates, creates the Jira ticket with
> attached logs, and dispatches the email to the support mailbox.
>
> 13\. On Jira closure, the feedback service captures the resolution,
> drafts SOP content for human validation and re-indexes approved
> content into the knowledge base.

**2.3 Technology Stack**

  ------------------ ----------------------- ----------------------------
  **Concern**        **Selection**           **Notes**

  Orchestration      Python (FastAPI)        Aligns with existing
  service            service                 reconciliation pipeline
                                             tooling

  Generation model   GPT-5.1 via internal AI Parser, Diagnostic and
                     platform                Template agents

  Embedding model    text-embedding-small    KB indexing and query
                     via internal AI         embedding
                     platform                

  Vector store       Approved internal       Selection subject to
                     vector database or      platform standards review
                     pgvector                

  Relational store   Incident, audit and     Incident state machine and
                     config tables           audit ledger

  Queue              Durable message queue   Decouples ingestion from
                     with retry and DLQ      agent processing

  Object storage     AWS S3                  Execution logs and working
                                             directory artefacts

  Review UI          React front end over    Consistent with existing
                     FastAPI                 dashboard stack

  Identity           JWT via OpenAM, DSP     Auto-refresh background task
                     token translation       

  Ticketing          Jira REST API           L2 support queue

  Secrets            Approved secrets        No credentials in code,
                     manager                 config or prompts
  ------------------ ----------------------- ----------------------------

**3. Data Model**

**3.1 Incident Record**

  -------------------- ------------ ----------------------------------------
  **Field**            **Type**     **Description**

  incident_id          UUID         Primary key, generated at ingestion

  bot_id / job_run_id  String       Genie Bot identifiers from the log path
                                    and header

  user_id              String       End user who ran the bot

  environment          Enum         Production, UAT or development

  log_s3_uri           String       Source execution log location

  ingested_at          Timestamp    Pipeline entry time for latency
                                    measurement

  status               Enum         See state machine in 3.2

  error_signature_id   String       Hash of normalised exception type plus
                                    failing module

  parse_output         JSON         Structured output of the Log Parser
                                    Agent

  diagnosis            JSON         Root cause, proposed resolution,
                                    citations, confidence

  template_payload     JSON         Pre-filled escalation template fields

  reviewer_id /        String /     Human-in-the-loop outcome
  decision             Enum         

  jira_key             String       Created or linked Jira issue key

  token_cost_usd       Decimal      Aggregate model cost for the incident
  -------------------- ------------ ----------------------------------------

**3.2 Incident State Machine**

INGESTED to SCREENED to PARSED to DIAGNOSED to (AUTO_RESOLVE_CANDIDATE
or ESCALATION_DRAFTED) to AWAITING_REVIEW to (SUBMITTED or REJECTED or
RERUN_APPROVED) to CLOSED. Terminal failure states:
BLOCKED_BY_GUARDRAIL, UNPARSEABLE and PLATFORM_UNAVAILABLE, each routing
to MANUAL_FALLBACK.

**3.3 Knowledge Base Chunk Record**

  ------------------ ------------ ----------------------------------------
  **Field**          **Type**     **Description**

  chunk_id           UUID         Primary key

  doc_id             UUID         Parent source document

  doc_type           Enum         resolved_incident, sop, template,
                                  runbook

  content            Text         Redacted chunk text that is embedded

  embedding          Vector       text-embedding-small output

  error_category     String       Taxonomy label for filtered retrieval

  source_ref         String       Jira key, SOP identifier or template
                                  name

  effective_from /   Timestamp    Supersession control; retired chunks
  retired_at                      excluded from retrieval

  classification     Enum         Data classification outcome; only
                                  approved levels indexed

  index_version      String       Enables versioned rebuild and rollback
  ------------------ ------------ ----------------------------------------

**4. Knowledge Base Creation**

The knowledge base is the component that determines diagnosis quality.
It is built once as an initial corpus and then maintained continuously
by the feedback loop. Both paths use the same ingestion pipeline so that
content is treated identically regardless of origin.

**4.1 Source Inventory**

  ------------------- ------------------ --------------------------------
  **Source**          **Content**        **Expected Use**

  Historical Jira     Closed Genie Bot   Primary diagnosis precedent
  tickets             support issues     
                      with resolutions   

  Genie Support       Historical support Template precedent and phrasing
  Mailbox archive     templates and      
                      email threads      

  Existing SOPs and   Documented         Authoritative resolution steps
  runbooks            operational        
                      procedures         

  Known error         Exception to root  Deterministic hints for common
  catalogue           cause mappings     failures

  Historical          Representative     Signature matching examples
  execution logs      stack traces per   
                      error class        
  ------------------- ------------------ --------------------------------

**4.2 Build Pipeline**

> 1\. Extract: pull source records via Jira API, mailbox export,
> document repository and S3 log samples into a staging area.
>
> 2\. Normalise: convert each source to a common document envelope with
> doc_type, source_ref, timestamps and raw text.
>
> 3\. Redact: apply the same redaction engine used at runtime ---
> credentials, connection strings, account identifiers and personal data
> removed before any further processing.
>
> 4\. Classify: run data classification; documents above the approved
> sensitivity threshold are excluded and logged as excluded, not
> silently dropped.
>
> 5\. Deduplicate: hash-based exact match plus fuzzy similarity to
> remove repeated tickets and forwarded email chains.
>
> 6\. Curate: subject matter review of a sample to confirm resolutions
> are correct and current; incorrect or obsolete records are marked
> retired rather than deleted.
>
> 7\. Label: assign error_category from the agreed taxonomy, enabling
> category-filtered retrieval and staged rollout.
>
> 8\. Chunk: split by semantic boundary --- problem statement,
> diagnosis, resolution steps --- rather than fixed character windows,
> preserving one coherent idea per chunk.
>
> 9\. Embed: generate vectors with text-embedding-small; batch requests
> to control cost and rate limits.
>
> 10\. Index: upsert chunks with metadata into the vector store under a
> new index_version.
>
> 11\. Evaluate: run the retrieval evaluation set against the new index;
> promote only if recall and precision meet or exceed the current index.
>
> 12\. Promote or roll back: switch the active index pointer on success;
> retain the previous version for rollback.

**4.3 Chunking Guidance**

- One chunk should answer one question --- what failed, why it failed,
  or how it was fixed.

- Keep the exception signature in the same chunk as its resolution so
  retrieval returns actionable content.

- Retain a short parent-document summary in each chunk\'s metadata to
  preserve context when a chunk is retrieved alone.

- Avoid chunking mid stack trace; truncate long traces to the
  distinctive frames rather than splitting them.

**4.4 Retrieval Design**

  ------------------- ---------------------------------------------------
  **Aspect**          **Design**

  Query construction  Embed the normalised error signature plus failing
                      module, not the raw log

  Filtering           Restrict by error_category and environment where
                      known; exclude retired chunks

  Top-N               Configurable; tuned during calibration against the
                      evaluation set

  Re-ranking          Prefer recent, higher-confidence resolutions where
                      similarity scores are close

  Return contract     Chunk text with source_ref and timestamp so the
                      Diagnostic Agent can cite

  Empty result        No qualifying match returns an explicit
  handling            no-precedent signal, lowering confidence
  ------------------- ---------------------------------------------------

**4.5 Continuous Maintenance**

- Closed Jira resolutions and reviewer edits are captured as new
  candidate documents.

- Recurring patterns trigger SOP draft generation, which requires human
  approval before indexing.

- Reviewer rejections are recorded as negative signal and used in
  evaluation, not indexed as guidance.

- Superseded SOPs are marked retired with retired_at set; retrieval
  excludes them without deleting history.

- Index rebuilds are versioned; a regression in evaluation metrics
  triggers rollback to the prior index_version.

**4.6 Knowledge Base Acceptance Criteria**

- Initial corpus covers the agreed error taxonomy with a minimum number
  of validated examples per category.

- Zero unredacted credentials or personal data detected in a sampled
  audit of indexed chunks.

- Retrieval recall on the labelled evaluation set meets the threshold
  agreed at Phase 0.

- Every indexed chunk carries a resolvable source_ref for traceability.

**5. Agent Design**

**5.1 Common Agent Contract**

- Each agent receives a typed input object and returns a typed output
  object; free-form text is not passed between agents.

- Model responses are requested as strict JSON with no prose or code
  fences, then schema-validated before use.

- Schema validation failure triggers one bounded retry with a corrective
  instruction, then fails closed to manual.

- Every agent invocation records prompt version, model identifier, token
  counts and latency to the audit ledger.

- Temperature is set low for deterministic extraction and diagnosis
  tasks.

**5.2 Log Parser Agent**

Input: redacted log text and incident metadata. Output: structured error
signature.

> {
>
> \"exception_type\": \"\...\",
>
> \"exception_message\": \"\...\",
>
> \"failing_module\": \"\...\",
>
> \"root_frame\": \"\...\",
>
> \"cascading_errors\": \[\"\...\"\],
>
> \"evidence_lines\": \[{\"line_no\": 0, \"text\": \"\...\"}\],
>
> \"parse_status\": \"parsed \| unparseable\"
>
> }

- Must distinguish the root exception from downstream cascading
  failures.

- Must return evidence_lines for every populated field to enable
  grounding verification.

- Returns unparseable rather than guessing when no recognisable error
  signature is present.

**5.3 Diagnostic Agent (L1 Triage)**

Input: parse output plus retrieved KB chunks. Output: diagnosis with
citations and confidence.

> {
>
> \"root_cause\": \"\...\",
>
> \"proposed_resolution\": \"\...\",
>
> \"resolution_type\": \"guidance \| controlled_rerun \| escalate\",
>
> \"rerun_parameters\": {},
>
> \"citations\": \[{\"type\": \"log\|kb\", \"ref\": \"\...\"}\],
>
> \"confidence\": 0.0,
>
> \"insufficient_information\": false
>
> }

- Prompt instructs the model to answer only from supplied evidence and
  to set insufficient_information where evidence is inadequate.

- resolution_type of controlled_rerun requires explicit rerun_parameters
  for human approval; the agent never initiates a rerun.

- Confidence is emitted by the model and calibrated against the labelled
  set; the raw score is not trusted until calibration is complete.

**5.4 Confidence Gate**

Deterministic code, not a model call. Evaluates confidence against the
configured threshold, presence of citations, insufficient_information
flag and error_category rollout status. Any failing condition forces the
escalation path. Thresholds are externally configurable and version
controlled.

**5.5 Template Generator Agent**

Input: parse output, diagnosis, retrieved template precedents. Output:
template field map conforming to the approved schema.

- Populates mandatory fields only from supported evidence; unsupported
  fields are set to an explicit unknown marker.

- Attaches log excerpts and S3 references supporting the escalation.

- Output is validated against the approved template schema before it
  reaches the reviewer.

**5.6 Integration Agent**

Deterministic orchestration with no generative step at submission time.
Maps the approved template to Jira fields, attaches log references,
dispatches the email, and applies deduplication before creation.

**6. Guardrail Implementation**

**6.1 Input Guardrails**

  ------------------ ----------------------------------------------------
  **Control**        **Implementation**

  Redaction          Pattern and entity based scrubbing of credentials,
                     connection strings, tokens, account and personal
                     identifiers prior to any model call

  Data               Classification tagging with block above approved
  classification     threshold; excluded content logged with reason

  Injection          Detection of instruction-like content embedded in
  screening          log or user-supplied text; suspicious content
                     neutralised or routed to manual

  Payload capping    Deterministic truncation and chunking strategy with
                     a hard token ceiling per invocation

  Fail closed        Any screening error routes the incident to
                     MANUAL_FALLBACK rather than proceeding
  ------------------ ----------------------------------------------------

**6.2 Output Guardrails**

  ------------------ ----------------------------------------------------
  **Control**        **Implementation**

  Grounding check    Each citation is resolved against actual parsed
                     evidence or an indexed chunk; unresolvable citations
                     block or flag the output

  Proposal-only      Pattern and intent screening blocks output
                     containing executable remediation directed at
                     production systems

  Sensitive data     Re-screening of generated content, including text
  egress             reproduced from logs, before human presentation

  Moderation         Content moderation screening on every generated
                     output

  Budget ceiling     Per-incident cumulative token and cost cap across
                     all agent calls, halting on breach

  Loop cap           Maximum controlled rerun iterations per incident to
                     prevent unbounded recursion
  ------------------ ----------------------------------------------------

**6.3 Guardrail Observability**

Every guardrail evaluation records the rule identifier, outcome and
disposition. Trigger rates are reported per rule so that over-blocking
and under-blocking are both visible. A material change in trigger rate
is treated as a signal of drift in either logs or model behaviour.

**7. Interface Specifications**

**7.1 Internal Service Endpoints**

  -------------------------- ------------ --------------------------------------
  **Endpoint**               **Method**   **Purpose**

  /incidents                 GET          List incidents with status and filter
                                          parameters

  /incidents/{id}            GET          Retrieve full incident detail
                                          including diagnosis and evidence

  /incidents/{id}/review     POST         Submit reviewer decision: approve,
                                          reject or approve rerun

  /incidents/{id}/template   PUT          Persist reviewer edits to template
                                          fields

  /kb/search                 POST         Diagnostic retrieval against the
                                          active index

  /kb/documents              POST         Submit approved content for indexing

  /kb/index/promote          POST         Promote a candidate index version
                                          after evaluation

  /admin/config              GET / PUT    Read and update thresholds, prompts
                                          and guardrail rules

  /admin/killswitch          POST         Enable or disable automated processing

  /health                    GET          Liveness and dependency status
  -------------------------- ------------ --------------------------------------

**7.2 External Integrations**

  ---------------- ------------------------------------------------------
  **System**       **Interaction**

  AWS S3           Event notification inbound; read of execution logs and
                   working directory artefacts

  Internal AI      Chat completion calls for GPT-5.1 and embedding calls
  platform         for text-embedding-small, authenticated by JWT

  Jira             REST API issue creation, attachment, search for
                   deduplication and closure webhook for feedback

  Mail service     Dispatch of alert and completed template to the Genie
                   Support Mailbox

  OpenAM / DSP     Token issuance and translation with background refresh
  ---------------- ------------------------------------------------------

**7.3 Deduplication Logic**

> 1\. Compute the error_signature_id from normalised exception type,
> failing module and bot identifier.
>
> 2\. Exact match: search open incidents and Jira issues for the same
> signature within the configured window.
>
> 3\. Fuzzy match: similarity comparison of summary text above the
> configured threshold.
>
> 4\. On match, link or append to the existing ticket and mark the
> incident as linked rather than creating a duplicate.
>
> 5\. Record the matching basis in the audit ledger so deduplication
> decisions are reviewable.

**8. Deployment & Environments**

**8.1 Environments**

  ----------------- ----------------------- --------------------------------
  **Environment**   **Purpose**             **Data**

  Development       Component build and     Synthetic and anonymised sample
                    unit test               logs only

  UAT               Integration, shadow     Copied historical logs,
                    mode, calibration       redacted; non-production Jira
                                            project

  Production        Staged live rollout     Live logs with runtime
                                            redaction; live L2 queue
  ----------------- ----------------------- --------------------------------

**8.2 Configuration Management**

- Prompts, confidence thresholds, guardrail rules, taxonomy and template
  schema are externalised configuration, versioned in source control.

- Configuration changes are promoted through environments with the same
  review as code.

- The active prompt version and index version are recorded on every
  incident for reproducibility.

**8.3 Release Approach**

- Shadow mode first: pipeline runs end to end but submission is disabled
  and outputs are compared against the manual process.

- Pilot: enabled for the narrowest, lowest-risk error category with the
  human gate mandatory on every incident.

- Expansion: additional categories enabled only after acceptance metrics
  are met for the preceding category.

- Auto-submission for any category requires separate formal approval
  supported by pilot evidence.

**9. Test Strategy**

  ---------------- ------------------------------------------------------
  **Test Type**    **Scope**

  Unit             Parsing helpers, redaction rules, signature hashing,
                   dedup matching, schema validation

  Contract         Agent output schema conformance; Jira and mail payload
                   mapping

  Golden set       Labelled historical incidents with known-correct root
                   causes; measures diagnosis accuracy and confidence
                   calibration

  Retrieval        Labelled query to expected chunk set; measures recall
  evaluation       and precision per index version

  Adversarial      Prompt injection in logs, credential leakage attempts,
                   malformed and truncated logs, oversized payloads

  Guardrail        Deliberate ungrounded and remediation-instruction
  negative         outputs to confirm blocking

  Resilience       AI platform outage, Jira outage, mail failure, queue
                   backlog, kill-switch activation

  Shadow           Parallel run against the manual process over an agreed
  comparison       period

  User acceptance  Production support review of usability, evidence
                   clarity and edit burden
  ---------------- ------------------------------------------------------

**9.1 Key Acceptance Metrics**

- Diagnosis accuracy against the golden set at or above the agreed
  threshold.

- False-resolution rate at or below the agreed ceiling, measured as
  approved diagnoses later found incorrect.

- Reviewer edit rate and rejection rate trending down across the pilot.

- Zero unredacted sensitive data findings in adversarial and sampled
  audits.

- Duplicate ticket rate no worse than the current manual baseline.

**10. Operations**

**10.1 Monitoring**

- Cost: token consumption and spend per model, module and incident, with
  budget alerting.

- Performance: end-to-end latency average and P99, throughput,
  per-module error rates.

- Quality: auto-resolution rate, escalation rate, reviewer edit and
  rejection rates, golden-set accuracy.

- Safety: guardrail trigger rates by rule, moderation and classification
  outcomes.

- Drift: emerging error signatures with no KB precedent, and accuracy
  trend over time.

**10.2 Alerting Triggers**

- Budget threshold breach or anomalous per-incident cost.

- Sustained rise in reviewer rejection rate or guardrail block rate.

- Queue backlog beyond the agreed depth, or repeated fallback to manual
  routing.

- Retrieval evaluation regression following an index promotion.

**10.3 Runbook Scenarios**

  --------------------- -------------------------------------------------
  **Scenario**          **Response**

  Systematic            Activate kill-switch, roll back prompt or index
  misdiagnosis          version, re-run golden set before re-enabling

  Duplicate ticket      Disable Integration Agent submission, review
  flooding              dedup thresholds, reconcile created tickets

  Guardrail failure or  Halt processing immediately, treat as a security
  bypass                incident, audit affected incidents

  AI platform           Automatic fallback routing to manual L2; confirm
  unavailable           no incidents lost from the queue

  KB regression after   Roll back index pointer to prior index_version
  promotion             and re-evaluate
  --------------------- -------------------------------------------------

**11. Development Plan**

  ----------- ----------------------- ---------------------- --------------
  **Phase**   **Workstream**          **Key Deliverables**   **Exit
                                                             Criteria**

  0           Scoping                 Taxonomy, success      Metrics and
                                      metrics, auto vs       boundary
                                      escalate boundary      agreed

  1           Foundation              Ingestion, incident    Incidents flow
                                      model, queue, audit    end to end
                                      ledger                 with no agents

  2           Knowledge base          Source extraction,     Index meets
                                      redaction, curation,   retrieval
                                      chunking, indexing,    acceptance
                                      evaluation set         criteria

  3           Guardrails              Input and output       Negative tests
                                      guardrail services,    pass;
                                      fail-closed routing    moderation
                                                             gaps
                                                             remediated

  4           Agents                  Parser, Diagnostic,    Schema-valid
                                      Confidence Gate,       outputs on
                                      Template Generator     sample corpus

  5           Human-in-the-loop       Review UI, edit,       UAT sign-off
                                      approve, reject, rerun from
                                      approval               production
                                                             support

  6           Integration             Jira creation, mail    No duplicates
                                      dispatch,              in regression
                                      deduplication          run

  7           Feedback loop           Resolution capture,    New content
                                      SOP drafting,          improves
                                      re-indexing with       evaluation
                                      approval               metrics

  8           Observability           Dashboards, alerting,  Metrics
                                      audit reporting        visible to
                                                             governance
                                                             stakeholders

  9           Governance              Security, privacy and  Formal
                                      model risk review      sign-off
                                                             obtained

  10          Shadow & pilot          Parallel run,          Acceptance
                                      calibration, narrow    metrics met
                                      category pilot         

  11          Rollout                 Category-by-category   Kill-switch
                                      expansion, resilience  tested;
                                      rehearsal              runbook
                                                             published
  ----------- ----------------------- ---------------------- --------------

**12. Risks & Mitigations**

  --------------------- ------------------------- ------------------------
  **Risk**              **Impact**                **Mitigation**

  Plausible but wrong   Misdirected support       Grounding check,
  diagnosis             effort, incorrect closure confidence gate,
                                                  mandatory human review,
                                                  golden-set monitoring

  Sensitive data        Data protection breach    Pre-model redaction,
  reaching the model                              classification block,
                                                  fail closed, egress
                                                  re-screening

  Prompt injection via  Agent behaviour subverted Injection screening,
  log content                                     typed schema outputs,
                                                  proposal-only
                                                  enforcement

  Knowledge base decay  Accuracy degradation over Supersession control,
                        time                      retrieval evaluation per
                                                  index version, drift
                                                  alerting

  Runaway cost from     Budget breach             Per-incident budget
  rerun loops                                     ceiling and loop
                                                  iteration cap

  Over-reliance on the  Reduced analyst scrutiny  Confidence and evidence
  assistant                                       displayed prominently;
                                                  sampling audit of
                                                  approved incidents

  Production prompts    Cost above estimate       Per-incident cost
  larger than ideation                            telemetry with alerting;
                                                  estimate revised after
                                                  pilot
  --------------------- ------------------------- ------------------------

**13. Open Items**

- Latency and availability targets to be confirmed with production
  support.

- Confidence threshold and top-N retrieval values to be set during
  calibration against the labelled set.

- Vector store selection to be confirmed against internal platform
  standards.

- Error taxonomy to be finalised and agreed before knowledge base
  labelling begins.

- Acceptance thresholds for diagnosis accuracy and false-resolution rate
  to be formally agreed at Phase 0.
