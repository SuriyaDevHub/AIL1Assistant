import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getIncident, getJiraTicket, simulateJiraClosure } from "../api/client";
import PipelineView from "../components/PipelineView";
import PrecedentBanner from "../components/PrecedentBanner";
import ReviewPanel from "../components/ReviewPanel";
import { usePolling } from "../hooks/usePolling";

const POLL_INTERVAL_MS = 4000;
const TERMINAL_STATUSES = new Set(["CLOSED", "MANUAL_FALLBACK"]);

const OUTCOME_LABELS = {
  resolved: "Resolved (L1 fix confirmed)",
  escalated: "Escalated to L2",
  approve: "Approved",
  reject: "Rejected",
  approve_rerun: "Controlled rerun approved",
};

function _outcomeLabel(decision) {
  if (!decision) return "";
  return OUTCOME_LABELS[decision] ?? decision;
}

export default function IncidentDetail() {
  const { id } = useParams();
  const [incident, setIncident] = useState(null);
  const [error, setError] = useState(null);

  const reload = useCallback(() => {
    if (!id) return;
    getIncident(id).then(setIncident).catch((e) => setError(String(e)));
  }, [id]);

  useEffect(() => reload(), [reload]);

  const [jiraTicket, setJiraTicket] = useState(null);
  const [jiraError, setJiraError] = useState(false);
  const jiraKey = incident?.jira_key ?? null;
  useEffect(() => {
    if (!id || !jiraKey) {
      setJiraTicket(null);
      return;
    }
    setJiraError(false);
    getJiraTicket(id)
      .then(setJiraTicket)
      .catch(() => setJiraError(true));
  }, [id, jiraKey]);

  // Live updates while the pipeline is still working the incident (a real
  // model call per agent step means this can take several real seconds) -
  // stops once it reaches a terminal state, since nothing left will change.
  const isTerminal = incident ? TERMINAL_STATUSES.has(incident.status) : false;
  usePolling(reload, POLL_INTERVAL_MS, !isTerminal);

  const [closing, setClosing] = useState(false);
  const handleSimulateClosure = useCallback(async () => {
    if (!id) return;
    setClosing(true);
    try {
      await simulateJiraClosure(id);
      reload();
    } finally {
      setClosing(false);
    }
  }, [id, reload]);

  if (error) return <p className="error">{error}</p>;
  if (!incident) return <p>Loading...</p>;

  const { parse_output, diagnosis, template_payload } = incident;

  return (
    <div className="incident-detail">
      <div className="detail-header">
        <h2>
          {incident.bot_id} / {incident.job_run_id}
        </h2>
        <span className={`badge status-${incident.status.toLowerCase()}`}>{incident.status}</span>
      </div>

      <PipelineView incidentId={incident.incident_id} isTerminal={isTerminal} />

      {incident.status === "SUBMITTED" && incident.jira_key && (
        <p className="submitted-note">
          Escalated to L2 - ticket <strong>{incident.jira_key}</strong> is open. This incident stays open
          until the ticket closes.{" "}
          <button type="button" onClick={handleSimulateClosure} disabled={closing}>
            {closing ? "Closing..." : "Simulate L2 closing this ticket"}
          </button>
        </p>
      )}

      <dl className="meta-grid">
        <dt>Incident</dt>
        <dd className="mono">{incident.incident_id}</dd>
        <dt>Environment</dt>
        <dd>{incident.environment}</dd>
        <dt>Log</dt>
        <dd className="mono">{incident.log_s3_uri}</dd>
        <dt>Jira</dt>
        <dd>{incident.jira_key ?? "-"}</dd>
        <dt>Token cost</dt>
        <dd>${incident.token_cost_usd}</dd>
        <dt>Rerun count</dt>
        <dd>{incident.rerun_count}</dd>
      </dl>

      <section>
        <h3>Parsed error signature</h3>
        {parse_output ? (
          <>
            <p>
              <strong>{parse_output.exception_type}</strong> in{" "}
              <code>{parse_output.failing_module}</code>
            </p>
            <p>{parse_output.exception_message}</p>
            <h4>Evidence</h4>
            <ul className="evidence-list">
              {parse_output.evidence_lines.map((e) => (
                <li key={e.line_no}>
                  <span className="line-no">{e.line_no}</span>
                  <code>{e.text}</code>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="empty">Not yet parsed.</p>
        )}
      </section>

      <section>
        <h3>Diagnosis</h3>
        {diagnosis ? (
          <>
            <p className="confidence">
              Confidence: <strong>{(diagnosis.confidence * 100).toFixed(0)}%</strong>{" "}
              {diagnosis.insufficient_information && (
                <span className="warn">insufficient information</span>
              )}
            </p>
            <p>
              <strong>Root cause:</strong> {diagnosis.root_cause}
            </p>
            <p>
              <strong>Proposed resolution</strong> ({diagnosis.resolution_type}):{" "}
              {diagnosis.proposed_resolution}
            </p>
            <h4>Citations</h4>
            <ul>
              {diagnosis.citations.map((c, i) => (
                <li key={i}>
                  [{c.type}] {c.ref}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="empty">Not yet diagnosed.</p>
        )}
      </section>

      <section>
        <h3>Escalation template</h3>
        {template_payload ? (
          <dl className="meta-grid">
            <dt>Category</dt>
            <dd>{template_payload.error_category}</dd>
            <dt>Priority</dt>
            <dd>{template_payload.priority ?? "-"}</dd>
            <dt>Summary</dt>
            <dd>{template_payload.summary}</dd>
            <dt>Recommended action</dt>
            <dd>{template_payload.recommended_action}</dd>
            {template_payload.unknown_fields.length > 0 && (
              <>
                <dt>Unknown fields</dt>
                <dd className="warn">{template_payload.unknown_fields.join(", ")}</dd>
              </>
            )}
          </dl>
        ) : (
          <p className="empty">No template generated.</p>
        )}
      </section>

      {jiraKey && (
        <section>
          <h3>Jira ticket</h3>
          {jiraTicket ? (
            <>
              <dl className="meta-grid">
                <dt>Key</dt>
                <dd className="mono">{jiraTicket.key}</dd>
                <dt>Status</dt>
                <dd>{jiraTicket.status}</dd>
                <dt>Summary</dt>
                <dd>{jiraTicket.summary}</dd>
              </dl>
              {jiraTicket.labels.length > 0 && (
                <p className="chips">
                  {jiraTicket.labels.map((label) => (
                    <span key={label} className="chip">
                      {label}
                    </span>
                  ))}
                </p>
              )}
              <h4>Description</h4>
              <pre className="ticket-description">{jiraTicket.description}</pre>
              {jiraTicket.comments.length > 0 && (
                <>
                  <h4>Comments</h4>
                  <ul>
                    {jiraTicket.comments.map((comment, i) => (
                      <li key={i}>{comment}</li>
                    ))}
                  </ul>
                </>
              )}
            </>
          ) : jiraError ? (
            <p className="empty">Ticket details unavailable.</p>
          ) : (
            <p className="empty">Loading ticket...</p>
          )}
        </section>
      )}

      <PrecedentBanner incidentId={incident.incident_id} errorSignatureId={incident.error_signature_id} />

      {incident.status === "AWAITING_REVIEW" && (
        <ReviewPanel
          incidentId={incident.incident_id}
          resolutionType={diagnosis?.resolution_type}
          genericL1Checklist={incident.generic_l1_checklist}
          errorSignatureId={incident.error_signature_id}
          onDecided={reload}
        />
      )}

      {incident.reviewer_id && (
        <section>
          <h3>Outcome</h3>
          <p>
            {_outcomeLabel(incident.decision)} by {incident.reviewer_id}
            {incident.reviewer_comment && <> - "{incident.reviewer_comment}"</>}
          </p>
        </section>
      )}
    </div>
  );
}
