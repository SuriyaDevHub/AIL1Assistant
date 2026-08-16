import { useEffect, useState } from "react";
import { IncidentTimelineEntry, getIncidentTimeline } from "../api/client";

const TERMINAL_GOOD = new Set(["CLOSED"]);
const TERMINAL_CRITICAL = new Set(["MANUAL_FALLBACK", "BLOCKED_BY_GUARDRAIL", "UNPARSEABLE", "PLATFORM_UNAVAILABLE"]);
const AWAITING = new Set(["AWAITING_REVIEW", "SUBMITTED"]);
const TERMINAL = new Set([...TERMINAL_GOOD, ...TERMINAL_CRITICAL]);

const STAGE_LABELS: Record<string, string> = {
  INGESTED: "Ingested",
  SCREENED: "Screened",
  PARSED: "Parsed",
  DIAGNOSED: "Diagnosed",
  AUTO_RESOLVE_CANDIDATE: "L1 resolution",
  ESCALATION_DRAFTED: "Escalation drafted",
  AWAITING_REVIEW: "Awaiting review",
  RERUN_APPROVED: "Rerun approved",
  SUBMITTED: "Open in Jira",
  REJECTED: "Rejected",
  CLOSED: "Closed",
  BLOCKED_BY_GUARDRAIL: "Blocked by guardrail",
  UNPARSEABLE: "Unparseable",
  PLATFORM_UNAVAILABLE: "Platform unavailable",
  MANUAL_FALLBACK: "Manual fallback",
};

function stageLabel(status: string): string {
  return STAGE_LABELS[status] ?? status;
}

function stageClass(status: string, isLast: boolean): string {
  if (!isLast) return "stage stage-done";
  if (TERMINAL_GOOD.has(status)) return "stage stage-good";
  if (TERMINAL_CRITICAL.has(status)) return "stage stage-critical";
  if (AWAITING.has(status)) return "stage stage-current";
  return "stage stage-running";
}

function formatDuration(fromMs: number, toMs: number): string {
  const seconds = Math.max(0, Math.round((toMs - fromMs) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

// Jenkins-style stage strip: renders the incident's REAL, timestamped state
// history (from the audit ledger via GET /incidents/{id}/timeline), not a
// guessed fixed template - a still-in-progress incident just gets a
// trailing "running" marker after its last known stage, same as Jenkins
// shows executed stages plus a running indicator rather than hypothetical
// future ones.
export default function PipelineView({ incidentId, isTerminal }: { incidentId: string; isTerminal: boolean }) {
  const [entries, setEntries] = useState<IncidentTimelineEntry[] | null>(null);

  useEffect(() => {
    getIncidentTimeline(incidentId)
      .then(setEntries)
      .catch(() => setEntries([]));
  }, [incidentId, isTerminal]);

  if (!entries) return null;
  if (entries.length === 0) return <p className="empty">No stage history yet.</p>;

  const stillRunning = !isTerminal && !TERMINAL.has(entries[entries.length - 1].status);

  return (
    <div className="pipeline-view">
      {entries.map((entry, i) => {
        const isLastReal = i === entries.length - 1;
        const next = entries[i + 1];
        return (
          <div className={stageClass(entry.status, isLastReal && !stillRunning)} key={`${entry.status}-${entry.at}`}>
            <span className="stage-label">{stageLabel(entry.status)}</span>
            {next && <span className="stage-duration">{formatDuration(Date.parse(entry.at), Date.parse(next.at))}</span>}
          </div>
        );
      })}
      {stillRunning && (
        <div className="stage stage-running stage-pulse">
          <span className="stage-label">Running…</span>
        </div>
      )}
    </div>
  );
}
