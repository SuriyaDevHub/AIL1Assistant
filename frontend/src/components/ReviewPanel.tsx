import { useEffect, useState } from "react";
import { getIncidentPrecedent, reviewIncident } from "../api/client";

export default function ReviewPanel({
  incidentId,
  resolutionType,
  genericL1Checklist,
  errorSignatureId,
  onDecided,
}: {
  incidentId: string;
  resolutionType?: string;
  genericL1Checklist?: string[] | null;
  errorSignatureId?: string | null;
  onDecided: () => void;
}) {
  const [submitterId, setSubmitterId] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingEscalate, setConfirmingEscalate] = useState(false);

  // Only fetched to gate the one targeted extra confirm step below (an
  // already-open ticket for this exact issue) - PrecedentBanner does its
  // own separate fetch/render of the same endpoint for the informational
  // banner; this is intentionally not shared state, keeping the two
  // components' concerns independent (GenieBot L1 Enhancement Plan,
  // "reduce reflexive escalation without gating it").
  const [openPrecedentJiraKey, setOpenPrecedentJiraKey] = useState<string | null>(null);

  useEffect(() => {
    if (!errorSignatureId) {
      setOpenPrecedentJiraKey(null);
      return;
    }
    getIncidentPrecedent(incidentId)
      .then((p) => setOpenPrecedentJiraKey(p.open_precedent?.jira_key ?? null))
      .catch(() => setOpenPrecedentJiraKey(null));
  }, [incidentId, errorSignatureId]);

  const hasChecklist = !!genericL1Checklist && genericL1Checklist.length > 0;
  const hasL1Fix = resolutionType === "controlled_rerun" || resolutionType === "guidance" || hasChecklist;

  async function decide(decision: "resolved" | "escalated") {
    if (!submitterId) {
      setError("your name/ID is required");
      return;
    }
    if (decision === "escalated" && !comment.trim()) {
      setError("please describe what you tried or observed before escalating");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await reviewIncident(incidentId, { reviewer_id: submitterId, decision, comment: comment || undefined });
      onDecided();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function handleEscalateClick() {
    if (openPrecedentJiraKey && !confirmingEscalate) {
      if (!submitterId) {
        setError("your name/ID is required");
        return;
      }
      if (!comment.trim()) {
        setError("please describe what you tried or observed before escalating");
        return;
      }
      setError(null);
      setConfirmingEscalate(true);
      return;
    }
    decide("escalated");
  }

  return (
    <section className="review-panel">
      <h3>{hasL1Fix ? "Did the suggested fix resolve your issue?" : "No automated fix available"}</h3>
      <p className="empty">
        {hasChecklist
          ? "We don't have a specific precedent for this yet - try these general steps first, then let us know what happened."
          : hasL1Fix
            ? "Try the proposed resolution above, then let us know what happened."
            : "This error has no matching precedent, so there's nothing to try - submit to escalate to L2 support."}
      </p>
      {hasChecklist && (
        <ol className="generic-checklist">
          {genericL1Checklist!.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
      {error && <p className="error">{error}</p>}
      <div className="review-form">
        <input
          placeholder="your name / ID"
          value={submitterId}
          onChange={(e) => setSubmitterId(e.target.value)}
        />
        <textarea
          placeholder={
            hasL1Fix ? "comment (required if escalating)" : "comment - what happened when you looked into this?"
          }
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        {confirmingEscalate && (
          <p className="escalate-confirm">
            This is already tracked as <strong>{openPrecedentJiraKey}</strong>. Escalating adds your comment to
            that same ticket rather than opening a new one.{" "}
            <button type="button" className="link-button" onClick={() => setConfirmingEscalate(false)}>
              Cancel
            </button>
          </p>
        )}
        <div className="review-actions">
          {hasL1Fix && (
            <button disabled={busy} className="approve" onClick={() => decide("resolved")}>
              Yes, it's fixed
            </button>
          )}
          <button
            disabled={busy}
            className={hasL1Fix ? "escalate-secondary" : "rerun"}
            onClick={handleEscalateClick}
          >
            {confirmingEscalate
              ? "Yes, escalate anyway"
              : hasL1Fix
                ? "Still stuck? Escalate to L2"
                : "Escalate to L2"}
          </button>
        </div>
      </div>
    </section>
  );
}
