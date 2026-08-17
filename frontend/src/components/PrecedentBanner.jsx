import { useEffect, useState } from "react";
import { getIncidentPrecedent } from "../api/client";

// Surfaces prior incidents sharing this incident's error signature, before
// the end user decides resolved-vs-escalate - the backend already dedupes
// a later escalation onto an existing Jira ticket instead of creating a
// duplicate (integrations/dedup.py); this tells the end user that truth up
// front instead of leaving them to find out after the fact.
//
// errorSignatureId (not just incidentId) drives the fetch: IncidentDetail
// polls and re-renders as the pipeline progresses, but this component's own
// effect only needs to re-fire once the signature actually exists - keying
// on incidentId alone would fetch once while it's still null (pre-diagnosis)
// and never refetch once it's set.
export default function PrecedentBanner({ incidentId, errorSignatureId }) {
  const [precedent, setPrecedent] = useState(null);

  useEffect(() => {
    if (!errorSignatureId) {
      setPrecedent(null);
      return;
    }
    getIncidentPrecedent(incidentId)
      .then(setPrecedent)
      .catch(() => setPrecedent(null));
  }, [incidentId, errorSignatureId]);

  if (!precedent) return null;
  const { resolved_precedent, open_precedent } = precedent;
  if (!resolved_precedent && !open_precedent) return null;

  return (
    <div className="precedent-banner">
      {resolved_precedent && (
        <div className="precedent-card precedent-card-resolved">
          <strong>This looks like a known issue - it was previously resolved.</strong>
          <p>{resolved_precedent.resolution_summary}</p>
          <p className="precedent-meta">
            Confirmed by {resolved_precedent.reviewer_id ?? "a user"} on{" "}
            {new Date(resolved_precedent.at).toLocaleString()} (incident {resolved_precedent.job_run_id})
            {resolved_precedent.reviewer_comment && <> - "{resolved_precedent.reviewer_comment}"</>}
          </p>
          {resolved_precedent.within_dedup_window && resolved_precedent.jira_key && (
            <p className="precedent-caveat">
              If you escalate anyway, it will be added to that same ticket ({resolved_precedent.jira_key}) rather
              than opening a new one.
            </p>
          )}
        </div>
      )}
      {open_precedent && (
        <div className="precedent-card precedent-card-open">
          <strong>
            This issue is already open in Jira as {open_precedent.jira_key}
            {open_precedent.jira_status && <> (status: {open_precedent.jira_status})</>}.
          </strong>
          <p>
            If you escalate, your comment will be added to that same ticket rather than creating a new one.
          </p>
        </div>
      )}
    </div>
  );
}
