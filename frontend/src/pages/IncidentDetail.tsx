import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { IncidentDetail as IncidentDetailType, getIncident } from "../api/client";
import ReviewPanel from "../components/ReviewPanel";

export default function IncidentDetail() {
  const { id } = useParams<{ id: string }>();
  const [incident, setIncident] = useState<IncidentDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!id) return;
    getIncident(id).then(setIncident).catch((e) => setError(String(e)));
  }, [id]);

  useEffect(() => reload(), [reload]);

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

      {incident.status === "AWAITING_REVIEW" && (
        <ReviewPanel incidentId={incident.incident_id} onDecided={reload} />
      )}

      {incident.reviewer_id && (
        <section>
          <h3>Review</h3>
          <p>
            {incident.decision} by {incident.reviewer_id}
            {incident.reviewer_comment && <> - "{incident.reviewer_comment}"</>}
          </p>
        </section>
      )}
    </div>
  );
}
