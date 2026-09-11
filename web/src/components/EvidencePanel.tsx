/**
 * The evidence workbench.
 *
 * Evidence is retrieved content from the synthetic environment, and some documents deliberately
 * contain text aimed at an automated reader. It is shown, because a reviewer should see exactly what
 * the investigator saw, and it is labelled untrusted and rendered as escaped text. There is no
 * dangerouslySetInnerHTML anywhere in this file, and an ESLint rule forbids it repository-wide.
 */

import { useState } from "react";
import { getEvidence } from "@/api/client";
import { Badge, Banner, JsonBlock, Latency, Loading } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { useResource } from "@/hooks/useApi";
import type { EvidenceSummary } from "@/types/api";

export function EvidenceList({ evidence, selectedId, onSelect }: {
  evidence: EvidenceSummary[];
  selectedId?: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {evidence.map((item) => {
        const selected = item.evidence_id === selectedId;
        return (
          <li key={item.evidence_id}>
            <button
              type="button"
              onClick={() => onSelect(item.evidence_id)}
              aria-current={selected ? "true" : undefined}
              style={{
                display: "block", width: "100%", textAlign: "left", border: "none",
                borderBottom: "1px solid var(--border-hairline)",
                borderLeft: `2px solid ${selected ? "var(--accent)" : "transparent"}`,
                background: selected ? "var(--accent-soft)" : "transparent",
                padding: "var(--space-2) var(--space-4)", font: "inherit",
              }}
            >
              <span className="row" style={{ gap: 8 }}>
                <span className="mono small" style={{ fontWeight: 600 }}>{item.evidence_id}</span>
                <span className="xs dim">{item.source_kind}</span>
                {!item.ok && <Badge tone="danger">failed</Badge>}
                <span style={{ marginLeft: "auto" }}><Latency ms={item.latency_ms} /></span>
              </span>
              <span className="mono xs" style={{ display: "block", color: "var(--fg-muted)", marginTop: 2, wordBreak: "break-all" }}>
                {item.source_name}
              </span>
              <span className="xs dim" style={{ display: "block", marginTop: 1 }}>
                {item.result_kind ?? "error"} · {item.source_ids.length} record{item.source_ids.length === 1 ? "" : "s"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function EvidenceDetailView({ investigationId, evidenceId }: {
  investigationId: string;
  evidenceId: string;
}) {
  const { data, error, loading, reload } = useResource(
    () => getEvidence(investigationId, evidenceId),
    [investigationId, evidenceId],
  );
  const [showRaw, setShowRaw] = useState(false);

  if (error) return <ErrorState error={error} onRetry={reload} inline />;
  if (loading || !data) return <Loading label={`Loading ${evidenceId}`} rows={4} />;

  const item = data.evidence;
  return (
    <div className="stack">
      <div className="spread">
        <div className="row" style={{ gap: 8 }}>
          <span className="mono" style={{ fontWeight: 700, fontSize: "var(--text-md)" }}>{item.evidence_id}</span>
          <Badge>{item.source_kind}</Badge>
          {item.ok ? <Badge tone="ok">ok</Badge> : <Badge tone="danger">failed</Badge>}
        </div>
        <span className="xs dim mono">#{item.sequence} · {item.acquired_at.slice(11, 19)}</span>
      </div>

      <Banner tone="warn" title="Untrusted evidence">
        Retrieved content from the synthetic environment. It is displayed as data. Some documents in this
        world deliberately contain instructions aimed at an automated reader; those were refused by the
        action policy and are shown here only as evidence.
      </Banner>

      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px var(--space-4)", fontSize: "var(--text-sm)" }}>
        <dt className="dim">Source</dt>
        <dd className="mono" style={{ margin: 0, wordBreak: "break-all" }}>{item.source_name}</dd>
        {Object.keys(item.arguments).length > 0 && (
          <>
            <dt className="dim">Arguments</dt>
            <dd className="mono" style={{ margin: 0, wordBreak: "break-word" }}>
              {Object.entries(item.arguments).map(([key, value]) => (
                <span key={key} style={{ display: "block" }}>
                  {key}: {typeof value === "string" ? value : JSON.stringify(value)}
                </span>
              ))}
            </dd>
          </>
        )}
        <dt className="dim">World records</dt>
        <dd className="mono" style={{ margin: 0, wordBreak: "break-word" }}>
          {item.source_ids.length > 0 ? item.source_ids.join(", ") : <span className="dim">none</span>}
        </dd>
        <dt className="dim">Content hash</dt>
        <dd className="mono xs" style={{ margin: 0, wordBreak: "break-all", color: "var(--fg-muted)" }}>
          {item.content_hash}
        </dd>
        {item.error && (
          <>
            <dt className="dim">Error</dt>
            <dd style={{ margin: 0, color: "var(--danger)" }}>{item.error}</dd>
          </>
        )}
      </dl>

      {item.payload && <Summary payload={item.payload} />}

      {item.text && (
        <div>
          <div className="spread" style={{ marginBottom: 6 }}>
            <span className="small muted">Document text</span>
            <span className="xs dim">{item.text.length} characters</span>
          </div>
          {/* Escaped text in a <pre>. Never markup. */}
          <pre
            className="mono"
            style={{
              margin: 0, padding: "var(--space-3)", background: "var(--surface-sunken)",
              border: "1px solid var(--border-hairline)", borderRadius: "var(--radius-sm)",
              fontSize: "var(--text-sm)", lineHeight: 1.6, whiteSpace: "pre-wrap",
              wordBreak: "break-word", maxHeight: 380, overflow: "auto",
            }}
          >
            {item.text}
          </pre>
        </div>
      )}

      {item.payload && (
        <div>
          <button type="button" className="btn btn--ghost" onClick={() => setShowRaw((v) => !v)} aria-expanded={showRaw}>
            {showRaw ? "Hide" : "Show"} structured payload
          </button>
          {showRaw && <div style={{ marginTop: 8 }}><JsonBlock value={item.payload} maxHeight={420} /></div>}
        </div>
      )}
    </div>
  );
}

/** Tool results carry a human-readable summary; show it before the raw structure. */
function Summary({ payload }: { payload: Record<string, unknown> }) {
  const summary = payload.summary;
  if (typeof summary !== "string" || !summary) return null;
  return (
    <p style={{ margin: 0, fontSize: "var(--text-base)", lineHeight: 1.6, padding: "var(--space-3)",
      background: "var(--surface-sunken)", border: "1px solid var(--border-hairline)", borderRadius: "var(--radius-sm)" }}>
      {summary}
    </p>
  );
}
