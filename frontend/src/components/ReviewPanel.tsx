import { useState } from "react";
import { reviewIncident } from "../api/client";

export default function ReviewPanel({
  incidentId,
  onDecided,
}: {
  incidentId: string;
  onDecided: () => void;
}) {
  const [reviewerId, setReviewerId] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "approve" | "reject" | "approve_rerun") {
    if (!reviewerId) {
      setError("reviewer id is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await reviewIncident(incidentId, { reviewer_id: reviewerId, decision, comment: comment || undefined });
      onDecided();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="review-panel">
      <h3>Review decision</h3>
      {error && <p className="error">{error}</p>}
      <div className="review-form">
        <input
          placeholder="reviewer id"
          value={reviewerId}
          onChange={(e) => setReviewerId(e.target.value)}
        />
        <textarea
          placeholder="comment (optional)"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <div className="review-actions">
          <button disabled={busy} className="approve" onClick={() => decide("approve")}>
            Approve
          </button>
          <button disabled={busy} className="rerun" onClick={() => decide("approve_rerun")}>
            Approve rerun
          </button>
          <button disabled={busy} className="reject" onClick={() => decide("reject")}>
            Reject
          </button>
        </div>
      </div>
    </section>
  );
}
