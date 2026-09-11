/**
 * The hypothesis board.
 *
 * Hypotheses arrive live on the event stream, so this renders from events while an investigation is
 * running and from the API once it has finished. The revision history is the point: a reviewer
 * should be able to see a hypothesis proposed, then supported or refuted, and on what evidence.
 */

import { Confidence } from "@/components/primitives";
import { EvidenceLink, StatusPill } from "@/components/Timeline";
import type { Hypothesis, HypothesisStatus } from "@/types/api";

const TONE: Record<HypothesisStatus, string> = {
  proposed: "var(--h-proposed)",
  supported: "var(--h-supported)",
  weakened: "var(--h-weakened)",
  refuted: "var(--h-refuted)",
};

export function HypothesisBoard({ hypotheses, onSelectEvidence }: {
  hypotheses: Hypothesis[];
  onSelectEvidence?: (id: string) => void;
}) {
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {hypotheses.map((hypothesis) => (
        <li
          key={hypothesis.id}
          style={{ padding: "var(--space-3) var(--space-4)", borderBottom: "1px solid var(--border-hairline)" }}
        >
          <div className="row" style={{ gap: "var(--space-2)", marginBottom: 5 }}>
            <span className="mono" style={{ fontWeight: 700 }}>{hypothesis.id}</span>
            <StatusPill status={hypothesis.status} />
            <span style={{ marginLeft: "auto" }}>
              <Confidence value={hypothesis.confidence} tone={TONE[hypothesis.status]} />
            </span>
          </div>
          <p style={{ margin: "0 0 6px", fontSize: "var(--text-base)", lineHeight: 1.5 }}>{hypothesis.statement}</p>

          {hypothesis.supporting_evidence_ids.length > 0 && (
            <p className="xs" style={{ margin: "0 0 2px" }}>
              <span className="dim">supporting </span>
              {hypothesis.supporting_evidence_ids.map((id, index) => (
                <span key={id}>{index > 0 && ", "}<EvidenceLink id={id} onSelect={onSelectEvidence} /></span>
              ))}
            </p>
          )}
          {hypothesis.contradicting_evidence_ids.length > 0 && (
            <p className="xs" style={{ margin: "0 0 2px" }}>
              <span className="dim">contradicted by </span>
              {hypothesis.contradicting_evidence_ids.map((id, index) => (
                <span key={id}>{index > 0 && ", "}<EvidenceLink id={id} onSelect={onSelectEvidence} /></span>
              ))}
            </p>
          )}

          {hypothesis.revisions.length > 0 && <RevisionTrail hypothesis={hypothesis} />}
        </li>
      ))}
    </ul>
  );
}

/** The evolution, as a compact sparkline of steps with the confidence at each. */
function RevisionTrail({ hypothesis }: { hypothesis: Hypothesis }) {
  return (
    <div className="row row--wrap" style={{ gap: 4, marginTop: 6 }}>
      <span className="xs dim">evolution</span>
      {hypothesis.revisions.map((revision, index) => (
        <span key={`${revision.step}-${index}`} className="row" style={{ gap: 4 }}>
          {index > 0 && <span className="dim xs" aria-hidden="true">→</span>}
          <span
            className="xs mono"
            title={revision.note || `step ${revision.step}: ${revision.status} at ${revision.confidence.toFixed(2)}`}
            style={{
              border: "1px solid var(--border)", borderRadius: 3, padding: "0 4px",
              color: TONE[revision.status], background: "var(--surface-sunken)",
            }}
          >
            s{revision.step} {revision.status.slice(0, 4)} {revision.confidence.toFixed(2)}
          </span>
        </span>
      ))}
    </div>
  );
}
