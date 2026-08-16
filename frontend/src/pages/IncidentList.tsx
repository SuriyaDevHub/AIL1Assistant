import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IncidentListItem, listIncidents } from "../api/client";
import { usePolling } from "../hooks/usePolling";

const POLL_INTERVAL_MS = 4000;

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

  const fetchIncidents = useCallback((showSpinner: boolean) => {
    if (showSpinner) setLoading(true);
    listIncidents({ status: status || undefined })
      .then((data) => {
        setIncidents(data);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => showSpinner && setLoading(false));
  }, [status]);

  useEffect(() => {
    fetchIncidents(true);
  }, [fetchIncidents]);

  // Live updates: new incidents and status changes (e.g. the worker moving
  // one out of AWAITING_REVIEW) show up without a manual reload. No
  // spinner on background refreshes - only the initial/status-change load.
  usePolling(() => fetchIncidents(false), POLL_INTERVAL_MS);

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
