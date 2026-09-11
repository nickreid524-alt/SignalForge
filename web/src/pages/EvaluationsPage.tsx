/**
 * The evaluation dashboard.
 *
 * Every number on this page comes from the frozen benchmark the API serves. Nothing is hardcoded,
 * including the headline figures, so the page cannot drift away from the measured result.
 */

import { getBenchmark } from "@/api/client";
import { Badge, Banner, Confidence, EmptyState, Loading, Metric, Panel } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { useResource } from "@/hooks/useApi";
import type { BenchmarkScenario } from "@/types/api";

const pct = (value: number | undefined) => (value === undefined ? "—" : `${(value * 100).toFixed(value === 1 || value === 0 ? 0 : 1)}%`);

export function EvaluationsPage() {
  const { data, error, loading, reload } = useResource(() => getBenchmark("scripted"), []);

  if (error) return <div className="page"><ErrorState error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Panel title="Benchmark"><Loading rows={6} /></Panel></div>;

  const a = data.aggregates;
  const inconclusive = data.scenarios.filter((s) => s.expected_category === "inconclusive");

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Deterministic evaluation</h1>
          <p className="page__subtitle">
            Every scenario runs through the real pipeline: provider, orchestrator, MCP server, evidence
            registry and grounding validator. Scored against ground truth the investigator cannot reach.
          </p>
        </div>
        <div className="row">
          <Badge>{data.provider}</Badge>
          <Badge tone="ok">NO LLM</Badge>
          <span className="xs dim">frozen {data.generated_at}</span>
        </div>
      </div>

      <div className="stack">
        <div className="grid-4">
          <Metric
            label="Scenarios passed"
            value={`${a.passed ?? 0} / ${a.scenarios ?? data.scenario_count}`}
            hint={`${pct(a.pass_rate)} pass rate`}
            tone={a.pass_rate === 1 ? "ok" : "warn"}
          />
          <Metric label="Citation validity" value={pct(a.citation_validity_mean)}
            hint="every cited evidence id resolves" tone={a.citation_validity_mean === 1 ? "ok" : "warn"} />
          <Metric label="Unsupported claims" value={pct(a.unsupported_claim_rate_mean)}
            hint="claims without evidence" tone={a.unsupported_claim_rate_mean === 0 ? "ok" : "danger"} />
          <Metric label="Red herrings adopted" value={pct(a.red_herring_adoption_rate)}
            hint="misleading evidence taken as cause" tone={a.red_herring_adoption_rate === 0 ? "ok" : "danger"} />
          <Metric label="Decisive evidence recall" value={pct(a.decisive_evidence_recall_mean)}
            hint="of the evidence that settles the case" />
          <Metric label="Decisive citation recall" value={pct(a.decisive_citation_recall_mean)}
            hint="of that evidence, actually cited" />
          <Metric label="MCP tool calls" value={String(a.tool_calls_total ?? 0)}
            hint={`${(a.tool_calls_mean ?? 0).toFixed(1)} per scenario`} />
          <Metric label="Repair rounds" value={String(a.repair_rounds_total ?? 0)}
            hint="reports that failed grounding first time" tone={a.repair_rounds_total === 0 ? "ok" : "warn"} />
        </div>

        <Banner tone="info">
          {data.note}
        </Banner>

        {inconclusive.length > 0 && (
          <Panel title="Calibration: the scenario with no answer">
            <p style={{ margin: "0 0 var(--space-2)", lineHeight: 1.65 }}>
              {inconclusive.map((s) => s.scenario_id).join(", ")} has no determinable root cause in the evidence the
              environment exposes. The correct behaviour is to say so. A system that returned a confident cause here
              would be wrong in the way that matters most, so this scenario passes only when the report is{" "}
              <strong>inconclusive</strong> with low confidence and explicit unknowns.
            </p>
            <div className="row row--wrap">
              {inconclusive.map((scenario) => (
                <span key={scenario.scenario_id} className="row" style={{ gap: 8 }}>
                  <Badge tone={scenario.passed ? "ok" : "danger"}>{scenario.scenario_id}</Badge>
                  <span className="small muted">predicted {scenario.predicted_category ?? "—"}</span>
                  {scenario.confidence !== null && <Confidence value={scenario.confidence} tone="var(--warn)" />}
                  <Badge tone={scenario.calibration_ok ? "ok" : "danger"}>
                    {scenario.calibration_ok ? "calibrated" : "miscalibrated"}
                  </Badge>
                </span>
              ))}
            </div>
          </Panel>
        )}

        <Panel title={`Scenarios (${data.scenarios.length})`} flush>
          {data.scenarios.length === 0 ? (
            <EmptyState title="No scenarios in this benchmark" inline />
          ) : (
            <table className="table">
              <caption className="visually-hidden">Per-scenario evaluation results</caption>
              <thead>
                <tr>
                  <th scope="col">Scenario</th>
                  <th scope="col">Failure type</th>
                  <th scope="col">Predicted</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Evidence recall</th>
                  <th scope="col">Citation recall</th>
                  <th scope="col">Tool calls</th>
                  <th scope="col">Repairs</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {data.scenarios.map((scenario) => <ScenarioRow key={scenario.scenario_id} scenario={scenario} />)}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}

function ScenarioRow({ scenario }: { scenario: BenchmarkScenario }) {
  const matched = scenario.predicted_category === scenario.expected_category;
  return (
    <tr>
      <td className="table__id">
        {scenario.scenario_id}
        <div className="xs dim" style={{ fontFamily: "var(--font-sans)", maxWidth: 260, whiteSpace: "normal" }}>
          {scenario.title}
        </div>
      </td>
      <td className="small">{scenario.expected_category.replace(/_/g, " ")}</td>
      <td className="small">
        <span className="row" style={{ gap: 6 }}>
          {scenario.predicted_category?.replace(/_/g, " ") ?? "—"}
          {matched ? <Badge tone="ok">match</Badge> : <Badge tone="danger">differs</Badge>}
        </span>
      </td>
      <td>{scenario.confidence === null ? <span className="dim">—</span> : <Confidence value={scenario.confidence} />}</td>
      <td className="table__num">
        {scenario.decisive_evidence_recall === null ? <span className="dim">n/a</span> : pct(scenario.decisive_evidence_recall)}
      </td>
      <td className="table__num">
        {scenario.decisive_citation_recall === null ? <span className="dim">n/a</span> : pct(scenario.decisive_citation_recall)}
      </td>
      <td className="table__num">{scenario.tool_calls}</td>
      <td className="table__num">{scenario.repair_rounds}</td>
      <td>
        <span className="row" style={{ gap: 6 }}>
          <Badge tone={scenario.passed ? "ok" : "danger"}>{scenario.passed ? "PASS" : "FAIL"}</Badge>
          {scenario.injection_resisted === true && <Badge tone="ok" title="A prompt-injection attempt was refused">injection resisted</Badge>}
        </span>
      </td>
    </tr>
  );
}
