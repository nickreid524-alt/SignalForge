/**
 * The investigation workspace: the flagship screen.
 *
 * Three columns. Left, the investigation's state and budget. Centre, the live timeline of what the
 * provider asked for and what MCP returned. Right, the hypothesis board and the evidence workbench.
 * While an investigation runs everything is driven by the SSE stream; once it finishes the tabs
 * switch to the report and the observable trace.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getEvidenceList, getInvestigation, getReport, getTrace } from "@/api/client";
import { Badge, Banner, Duration, EmptyState, Loading, Panel } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { EvidenceDetailView, EvidenceList } from "@/components/EvidencePanel";
import { HypothesisBoard } from "@/components/HypothesisBoard";
import { evidenceFromEvents, hypothesesFromEvents, recordCounts } from "@/app/fromEvents";
import { ReportView } from "@/components/ReportView";
import { Timeline } from "@/components/Timeline";
import { TraceView } from "@/components/TraceView";
import { usePolledResource, useResource } from "@/hooks/useApi";
import { useInvestigationStream } from "@/hooks/useInvestigationStream";
import type { EvidenceSummary, InvestigationDetail, InvestigationEvent } from "@/types/api";

type Tab = "timeline" | "report" | "trace";

export function InvestigationPage() {
  const { investigationId = "" } = useParams();
  const stream = useInvestigationStream(investigationId);
  const running = !stream.terminal;

  // The stream is the live source. Polling is the fallback that keeps the summary honest if the
  // stream dies, and it stops as soon as the investigation is terminal.
  const detail = usePolledResource(() => getInvestigation(investigationId), running, 2500, [investigationId]);
  const finished = detail.data?.terminal ?? false;

  const [tab, setTab] = useState<Tab>("timeline");
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null);

  // The terminal event stops the polling, so fetch once more: otherwise the summary keeps whatever
  // it last read mid-run and the screen claims the investigation is still gathering evidence.
  const reloadDetail = detail.reload;
  useEffect(() => {
    if (stream.terminal) reloadDetail();
  }, [stream.terminal, reloadDetail]);

  // Move to the report once the investigation finishes; the timeline stays one click away.
  useEffect(() => {
    if (finished && stream.terminal?.type === "investigation.completed") {
      setTab((current) => (current === "timeline" ? "report" : current));
    }
  }, [finished, stream.terminal]);

  const evidence = useResource(() => getEvidenceList(investigationId), [investigationId, finished]);
  const hypotheses = useMemo(() => {
    const fromStream = hypothesesFromEvents(stream.events);
    return fromStream.length > 0 ? fromStream : (detail.data?.hypotheses ?? []);
  }, [stream.events, detail.data]);

  const liveEvidence = useLiveEvidence(stream.events, evidence.data?.evidence ?? []);

  if (detail.error && !detail.data) {
    return <div className="page"><ErrorState error={detail.error} onRetry={detail.reload} /></div>;
  }

  const data = detail.data;

  return (
    <div className="page page--wide">
      <div className="page__header">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ marginBottom: 4 }}>
            <Link to="/incidents" className="small">Incidents</Link>
            <span className="dim small">/</span>
            {data?.incident_id
              ? <Link to={`/incidents/${data.incident_id}`} className="small mono">{data.incident_id}</Link>
              : <span className="small mono">{investigationId}</span>}
            <span className="dim small">/</span>
            <span className="small muted">investigation</span>
          </div>
          <h1 className="page__title">{data?.incident?.title ?? "Investigation"}</h1>
          <div className="row row--wrap" style={{ marginTop: 8 }}>
            <StatusBadge status={data?.status ?? "queued"} terminal={data?.terminal ?? false} />
            <Badge>{data?.provider ?? "—"}</Badge>
            {data?.uses_live_api ? <Badge tone="live">LIVE API</Badge> : <Badge tone="ok">NO LLM</Badge>}
            <StreamBadge status={stream.status} />
            <span className="mono xs dim">{investigationId}</span>
          </div>
        </div>
      </div>

      {stream.status === "error" && running && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <Banner tone="warn" title="The live event stream dropped">
            The page is falling back to polling, so the summary stays current. Reload to reconnect the stream;
            no events are lost, because the server replays from the last event id.
          </Banner>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "244px minmax(0, 1fr) 380px", gap: "var(--space-4)", alignItems: "start" }}
        className="workspace">
        <div className="stack">
          <Panel title="Investigation">
            {data ? <StateSummary data={data} /> : <Loading rows={5} />}
          </Panel>
        </div>

        <div className="stack">
          <Panel
            title={
              <div className="row" role="tablist" aria-label="Investigation views" style={{ gap: 2 }}>
                {(["timeline", "report", "trace"] as Tab[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="tab"
                    aria-selected={tab === value}
                    onClick={() => setTab(value)}
                    className="btn btn--ghost"
                    style={{
                      padding: "2px 9px", fontSize: "var(--text-sm)", textTransform: "uppercase",
                      letterSpacing: "0.06em", fontWeight: 600,
                      color: tab === value ? "var(--accent-strong)" : "var(--fg-muted)",
                      background: tab === value ? "var(--accent-soft)" : "transparent",
                      borderColor: tab === value ? "var(--accent-border)" : "transparent",
                    }}
                  >
                    {value === "trace" ? "Observable trace" : value}
                  </button>
                ))}
              </div>
            }
            actions={
              tab === "timeline" ? (
                <span className="xs dim mono">{stream.events.length} events</span>
              ) : undefined
            }
            flush
          >
            {tab === "timeline" && (
              stream.events.length === 0 ? (
                <EmptyState title={running ? "Waiting for the first event" : "No events recorded"} inline>
                  {running
                    ? "The investigation has been queued. Events appear here as the provider requests evidence."
                    : "This investigation produced no events."}
                </EmptyState>
              ) : (
                <div style={{ maxHeight: "calc(100vh - 260px)", overflowY: "auto" }}>
                  <Timeline events={stream.events} onSelectEvidence={setSelectedEvidence} />
                </div>
              )
            )}
            {tab === "report" && <ReportTab investigationId={investigationId} onSelectEvidence={setSelectedEvidence} ready={data?.report_available ?? false} />}
            {tab === "trace" && <TraceTab investigationId={investigationId} />}
          </Panel>
        </div>

        <div className="stack">
          <Panel title={`Hypotheses (${hypotheses.length})`} flush>
            {hypotheses.length === 0 ? (
              <EmptyState title="No hypotheses yet" inline>
                They appear once the provider has gathered enough evidence to propose one.
              </EmptyState>
            ) : (
              <HypothesisBoard hypotheses={hypotheses} onSelectEvidence={setSelectedEvidence} />
            )}
          </Panel>

          <Panel
            title={selectedEvidence ? `Evidence ${selectedEvidence}` : `Evidence (${liveEvidence.length})`}
            actions={selectedEvidence && (
              <button type="button" className="btn btn--ghost" onClick={() => setSelectedEvidence(null)}>
                Back to list
              </button>
            )}
            flush={!selectedEvidence}
          >
            {selectedEvidence ? (
              <EvidenceDetailView investigationId={investigationId} evidenceId={selectedEvidence} />
            ) : liveEvidence.length === 0 ? (
              <EmptyState title="No evidence yet" inline>
                Evidence appears as the provider calls MCP tools and reads resources.
              </EmptyState>
            ) : (
              <div style={{ maxHeight: 420, overflowY: "auto" }}>
                <EvidenceList evidence={liveEvidence} selectedId={selectedEvidence} onSelect={setSelectedEvidence} />
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function useLiveEvidence(events: InvestigationEvent[], loaded: EvidenceSummary[]): EvidenceSummary[] {
  return useMemo(() => {
    if (loaded.length > 0) return loaded;
    const counts = recordCounts(events);
    return evidenceFromEvents(events).map((row) => ({
      ...row,
      source_ids: Array.from({ length: counts.get(row.evidence_id) ?? 0 }, () => ""),
    }));
  }, [events, loaded]);
}

function StateSummary({ data }: { data: InvestigationDetail }) {
  const usage = data.usage;
  const rows: [string, React.ReactNode][] = [
    ["Steps", `${usage.steps} / ${data.budget.max_steps}`],
    ["Tool calls", `${usage.tool_calls} / ${data.budget.max_tool_calls}`],
    ["Resource reads", `${usage.resource_reads} / ${data.budget.max_resource_reads}`],
    ["Provider calls", `${usage.model_calls} / ${data.budget.max_model_calls}`],
    ["Evidence", data.evidence_count],
    ["Refused actions", usage.rejected_actions + usage.suppressed_duplicates],
    ["Elapsed", <Duration key="d" seconds={usage.elapsed_seconds} />],
  ];
  return (
    <div className="stack stack--tight">
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "1fr auto", gap: "5px var(--space-3)", fontSize: "var(--text-sm)" }}>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: "contents" }}>
            <dt className="dim">{label}</dt>
            <dd className="mono" style={{ margin: 0, textAlign: "right" }}>{value}</dd>
          </div>
        ))}
      </dl>

      <div style={{ borderTop: "1px solid var(--border-hairline)", paddingTop: "var(--space-2)", marginTop: 2 }}>
        <div className="xs dim">Tokens</div>
        <div className="mono small">
          {data.token_usage.reported
            ? `${data.token_usage.input_tokens} in / ${data.token_usage.output_tokens} out`
            : "not reported (no LLM)"}
        </div>
      </div>

      {data.validation.ok !== null && (
        <div style={{ borderTop: "1px solid var(--border-hairline)", paddingTop: "var(--space-2)" }}>
          <div className="xs dim">Grounding validation</div>
          <Badge tone={data.validation.ok ? "ok" : "danger"}>
            {data.validation.ok ? "validated" : `${data.validation.errors} errors`}
          </Badge>
        </div>
      )}

      {data.termination_reason && (
        <div style={{ borderTop: "1px solid var(--border-hairline)", paddingTop: "var(--space-2)" }}>
          <div className="xs dim">Budget</div>
          <div className="small">{data.termination_reason}</div>
        </div>
      )}

      {data.error && (
        <div className="banner banner--danger" style={{ marginTop: "var(--space-2)" }}>
          <span className="small">{data.error}</span>
        </div>
      )}
    </div>
  );
}

function ReportTab({ investigationId, onSelectEvidence, ready }: {
  investigationId: string;
  onSelectEvidence: (id: string) => void;
  ready: boolean;
}) {
  const { data, error, loading, reload } = useResource(() => getReport(investigationId), [investigationId, ready]);
  if (error) {
    return error.code === "report_not_ready" ? (
      <EmptyState title="The report is not ready" inline action={<button type="button" className="btn" onClick={reload}>Check again</button>}>
        SignalForge writes its report only after the investigation concludes and the grounding validator has
        run. Watch the timeline meanwhile.
      </EmptyState>
    ) : <ErrorState error={error} onRetry={reload} inline />;
  }
  if (loading || !data) return <Loading label="Loading report" rows={6} />;
  return <div style={{ padding: "var(--space-4)" }}><ReportView report={data} onSelectEvidence={onSelectEvidence} /></div>;
}

function TraceTab({ investigationId }: { investigationId: string }) {
  const { data, error, loading, reload } = useResource(() => getTrace(investigationId), [investigationId]);
  if (error) return <ErrorState error={error} onRetry={reload} inline />;
  if (loading || !data) return <Loading label="Loading trace" rows={6} />;
  return <div style={{ padding: "var(--space-4)" }}><TraceView trace={data} /></div>;
}

function StatusBadge({ status, terminal }: { status: string; terminal: boolean }) {
  const tone = status === "completed" ? "ok"
    : status === "failed" || status === "failed_validation" ? "danger"
    : status === "completed_with_warnings" ? "warn"
    : "accent";
  return (
    <Badge tone={tone}>
      {!terminal && <span className="dot" aria-hidden="true" />}
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

function StreamBadge({ status }: { status: string }) {
  if (status === "closed") return <Badge>stream closed</Badge>;
  if (status === "open") return <Badge tone="ok"><span className="dot" aria-hidden="true" />live</Badge>;
  if (status === "reconnecting") return <Badge tone="warn">reconnecting…</Badge>;
  if (status === "error") return <Badge tone="danger">stream lost</Badge>;
  return <Badge>connecting…</Badge>;
}
