import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IncidentListItem, listIncidents } from "../api/client";

const STATUS_OPTIONS = [
  "",
  "INGESTED",
  "SCREENED",
  "PARSED",
  "DIAGNOSED",
  "AUTO_RESOLVE_CANDIDATE",
  "ESCALATION_DRAFTED",
  "AWAITING_REVIEW",
  "SUBMITTED",
  "REJECTED",
  "RERUN_APPROVED",
  "CLOSED",
  "BLOCKED_BY_GUARDRAIL",
  "UNPARSEABLE",
  "PLATFORM_UNAVAILABLE",
  "MANUAL_FALLBACK",
];

export default function IncidentList() {
  const [incidents, setIncidents] = useState<IncidentListItem[]>([]);
  const [status, setStatus] = useState("AWAITING_REVIEW");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    listIncidents({ status: status || undefined })
      .then(setIncidents)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [status]);

  return (
    <div>
      <div className="toolbar">
        <label>
          Status{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s || "(all)"}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && <p>Loading...</p>}
      {error && <p className="error">{error}</p>}

      <table className="incident-table">
        <thead>
          <tr>
            <th>Bot</th>
            <th>Job run</th>
            <th>Environment</th>
            <th>Status</th>
            <th>Signature</th>
            <th>Jira</th>
            <th>Ingested</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((inc) => (
            <tr key={inc.incident_id}>
              <td>
                <Link to={`/incidents/${inc.incident_id}`}>{inc.bot_id}</Link>
              </td>
              <td>{inc.job_run_id}</td>
              <td>{inc.environment}</td>
              <td>
                <span className={`badge status-${inc.status.toLowerCase()}`}>{inc.status}</span>
              </td>
              <td className="mono">{inc.error_signature_id?.slice(0, 10) ?? "-"}</td>
              <td>{inc.jira_key ?? "-"}</td>
              <td>{new Date(inc.ingested_at).toLocaleString()}</td>
            </tr>
          ))}
          {!loading && incidents.length === 0 && (
            <tr>
              <td colSpan={7} className="empty">
                No incidents.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
