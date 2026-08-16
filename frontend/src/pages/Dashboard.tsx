import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IncidentStats, getIncidentStats } from "../api/client";
import { usePolling } from "../hooks/usePolling";

const POLL_INTERVAL_MS = 4000;

const AWAITING_KEYS = ["AWAITING_REVIEW"];
const BLOCKED_KEYS = ["MANUAL_FALLBACK", "BLOCKED_BY_GUARDRAIL", "UNPARSEABLE", "PLATFORM_UNAVAILABLE"];
const CLOSED_KEYS = ["CLOSED"];
const CONFIDENCE_BUCKET_ORDER = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"];
const CONFIDENCE_COLORS = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];

function sumKeys(counts: Record<string, number>, keys: string[]): number {
  return keys.reduce((total, k) => total + (counts[k] ?? 0), 0);
}

// Every status not in one of the three named buckets above is still moving
// through the pipeline (INGESTED, SCREENED, ..., RERUN_APPROVED) - grouped
// as a single neutral bucket rather than enumerating every transient state.
function inProgressCount(counts: Record<string, number>): number {
  const known = new Set([...AWAITING_KEYS, ...BLOCKED_KEYS, ...CLOSED_KEYS]);
  return Object.entries(counts)
    .filter(([status]) => !known.has(status))
    .reduce((total, [, count]) => total + count, 0);
}

function formatUsd(value: string): string {
  return `$${Number(value).toFixed(2)}`;
}

export default function Dashboard() {
  const [stats, setStats] = useState<IncidentStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    getIncidentStats().then(setStats).catch((e) => setError(String(e)));
  };
  useEffect(load, []);
  usePolling(load, POLL_INTERVAL_MS);

  if (error) return <p className="error">{error}</p>;
  if (!stats) return <p>Loading...</p>;

  const statusBars = [
    { label: "In progress", count: inProgressCount(stats.status_counts), color: "var(--status-neutral)" },
    { label: "Awaiting review", count: sumKeys(stats.status_counts, AWAITING_KEYS), color: "var(--status-warning)" },
    { label: "Blocked / failed", count: sumKeys(stats.status_counts, BLOCKED_KEYS), color: "var(--status-critical)" },
    { label: "Closed", count: sumKeys(stats.status_counts, CLOSED_KEYS), color: "var(--status-good)" },
  ];

  const confidenceBars = CONFIDENCE_BUCKET_ORDER.map((label, i) => ({
    label,
    count: stats.confidence_buckets[label] ?? 0,
    color: CONFIDENCE_COLORS[i],
  }));

  return (
    <div className="dashboard">
      <div className="stat-tiles">
        <StatTile label="Total incidents" value={stats.total_incidents} />
        <StatTile label="Open" value={stats.open_count} />
        <StatTile label="Escalated to L2" value={stats.decision_counts["escalated"] ?? 0} />
        <StatTile label="Closed" value={stats.status_counts["CLOSED"] ?? 0} />
        <StatTile label="Cost, last 24h" value={formatUsd(stats.cost_last_24h_usd)} />
        <StatTile label="Low-context escalations, 24h" value={stats.low_context_escalations_last_24h} />
      </div>

      <div className="chart-grid">
        <section className="chart-card">
          <h3>Status distribution</h3>
          <StatusBarChart bars={statusBars} />
        </section>

        <section className="chart-card">
          <h3>Diagnosis confidence</h3>
          <p className="empty">
            {stats.avg_confidence != null
              ? `Average: ${(stats.avg_confidence * 100).toFixed(0)}%`
              : "No diagnoses yet."}
          </p>
          <ConfidenceHistogram buckets={confidenceBars} />
        </section>
      </div>

      <section>
        <h3>Recent incidents</h3>
        {/* The full sortable/filterable table already exists at "/" - this
            is a glance list, not a duplicate of it. */}
        <ul className="recent-list">
          {stats.recent.map((incident) => (
            <li key={incident.incident_id}>
              <Link to={`/incidents/${incident.incident_id}`}>
                {incident.bot_id} / {incident.job_run_id}
              </Link>
              <span className={`badge status-${incident.status.toLowerCase()}`}>{incident.status}</span>
              <span className="muted">{new Date(incident.ingested_at).toLocaleString()}</span>
            </li>
          ))}
          {stats.recent.length === 0 && <li className="empty">No incidents yet.</li>}
        </ul>
      </section>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat-tile">
      <div className="stat-tile-value">{value}</div>
      <div className="stat-tile-label">{label}</div>
    </div>
  );
}

interface Bar {
  label: string;
  count: number;
  color: string;
}

// Horizontal bars: status names read as labels, so no separate legend box
// is needed (dataviz skill: a legend exists for identity readers can't get
// elsewhere - here every bar already carries its name as a direct label).
function StatusBarChart({ bars }: { bars: Bar[] }) {
  const max = Math.max(1, ...bars.map((b) => b.count));
  const barHeight = 18;
  const rowHeight = 28;
  const width = 340;
  const labelWidth = 110;
  const valueGutter = 28;
  const trackWidth = width - labelWidth - valueGutter;
  const height = bars.length * rowHeight;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="Incident status distribution">
      {bars.map((bar, i) => {
        const barLen = bar.count > 0 ? Math.max((bar.count / max) * trackWidth, 4) : 0;
        const y = i * rowHeight;
        return (
          <g key={bar.label}>
            <text x={0} y={y + barHeight / 2 + 4} fontSize="11" fill="var(--muted)">
              {bar.label}
            </text>
            <rect x={labelWidth} y={y} width={trackWidth} height={barHeight} rx={4} fill="var(--border)" opacity={0.4} />
            {barLen > 0 && (
              <rect x={labelWidth} y={y} width={barLen} height={barHeight} rx={4} fill={bar.color}>
                <title>{`${bar.label}: ${bar.count}`}</title>
              </rect>
            )}
            <text x={labelWidth + trackWidth + 8} y={y + barHeight / 2 + 4} fontSize="11" fill="var(--text)">
              {bar.count}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// Vertical columns, one hue stepped light->dark across the 5 ordered
// buckets (dataviz skill: ordinal data - reordering the buckets would
// change their meaning - takes a single-hue monotone-lightness ramp).
function ConfidenceHistogram({ buckets }: { buckets: Bar[] }) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const width = 340;
  const chartHeight = 120;
  const height = chartHeight + 34;
  const barWidth = 30;
  const gap = (width - buckets.length * barWidth) / (buckets.length + 1);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="Diagnosis confidence distribution">
      <line x1={0} y1={chartHeight} x2={width} y2={chartHeight} stroke="var(--border)" strokeWidth={1} />
      {buckets.map((bucket, i) => {
        const barHeight = bucket.count > 0 ? Math.max((bucket.count / max) * (chartHeight - 16), 4) : 0;
        const x = gap + i * (barWidth + gap);
        const y = chartHeight - barHeight;
        return (
          <g key={bucket.label}>
            {barHeight > 0 && (
              <rect x={x} y={y} width={barWidth} height={barHeight} rx={3} fill={bucket.color}>
                <title>{`Confidence ${bucket.label}: ${bucket.count}`}</title>
              </rect>
            )}
            {bucket.count > 0 && (
              <text x={x + barWidth / 2} y={y - 5} fontSize="10" fill="var(--text)" textAnchor="middle">
                {bucket.count}
              </text>
            )}
            <text x={x + barWidth / 2} y={chartHeight + 16} fontSize="9" fill="var(--muted)" textAnchor="middle">
              {bucket.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
