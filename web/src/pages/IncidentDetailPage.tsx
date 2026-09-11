/**
 * One incident, plus the launch control for an investigation.
 *
 * The provider selector reflects what the *server* has configured. It never offers a place to type a
 * key, and a provider the server has not enabled is disabled with the reason shown. When a live
 * provider is selected the call to action says so plainly, because that run costs money.
 */

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, createInvestigation, getIncident } from "@/api/client";
import { Badge, Banner, Loading, Panel, Severity } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { useResource } from "@/hooks/useApi";
import { useSystem } from "@/hooks/useSystem";
import type { BudgetProfile, ProviderName, ProviderSummary } from "@/types/api";

const PROVIDER_LABELS: Record<string, string> = {
  scripted: "Scripted demonstration",
  replay: "Replay (recorded)",
  anthropic: "Anthropic",
  openai: "OpenAI",
};

const BUDGETS: { value: BudgetProfile; label: string; hint: string }[] = [
  { value: "quick", label: "Quick", hint: "4 steps, 12 tool calls" },
  { value: "default", label: "Standard", hint: "8 steps, 24 tool calls" },
  { value: "thorough", label: "Thorough", hint: "10 steps, 30 tool calls" },
];

export function IncidentDetailPage() {
  const { incidentId = "" } = useParams();
  const { data, error, loading, reload } = useResource(() => getIncident(incidentId), [incidentId]);
  const { providers } = useSystem();
  const navigate = useNavigate();

  const [provider, setProvider] = useState<ProviderName>("scripted");
  const [budget, setBudget] = useState<BudgetProfile>("default");
  const [submitting, setSubmitting] = useState(false);
  const [launchError, setLaunchError] = useState<ApiError | null>(null);

  const available = providers?.providers ?? [];
  const selected = available.find((p) => p.name === provider);
  const usesLive = selected?.uses_live_api ?? false;
  const canLaunch = !!selected && (selected.name === "scripted" || (selected.enabled && selected.ready));

  async function launch() {
    setSubmitting(true);
    setLaunchError(null);
    try {
      const created = await createInvestigation({ incident_id: incidentId, provider, budget_profile: budget });
      navigate(`/investigations/${created.id}`);
    } catch (cause) {
      setLaunchError(cause instanceof ApiError ? cause : new ApiError("internal_error", String(cause), 0));
      setSubmitting(false);
    }
  }

  if (error) return <div className="page"><ErrorState error={error} onRetry={reload} /></div>;
  if (loading || !data) return <div className="page"><Panel title="Incident"><Loading rows={5} /></Panel></div>;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <div className="row" style={{ marginBottom: 4 }}>
            <Link to="/incidents" className="small">Incidents</Link>
            <span className="dim small">/</span>
            <span className="mono small">{data.id}</span>
          </div>
          <h1 className="page__title">{data.title}</h1>
          <div className="row row--wrap" style={{ marginTop: 8 }}>
            <Severity value={data.severity} />
            <Badge>{data.affected_service}</Badge>
            <span className="small muted">
              detected {data.detected_at.slice(0, 16).replace("T", " ")}Z · reported by {data.reporter}
            </span>
          </div>
        </div>
      </div>

      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="stack">
          <Panel title="Symptom">
            <p style={{ margin: 0, lineHeight: 1.65 }}>{data.description}</p>
          </Panel>

          <Panel title="Observable context">
            <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "10px var(--space-4)" }}>
              <dt className="small muted">Affected service</dt>
              <dd className="mono small" style={{ margin: 0 }}>{data.affected_service}</dd>
              <dt className="small muted">Detected</dt>
              <dd className="mono small" style={{ margin: 0 }}>{data.detected_at}</dd>
              <dt className="small muted">Investigation clock</dt>
              <dd className="mono small" style={{ margin: 0 }}>
                {data.investigation_clock}
                <span className="dim"> (treated as “now” during the investigation)</span>
              </dd>
              <dt className="small muted">Reporter</dt>
              <dd className="mono small" style={{ margin: 0 }}>{data.reporter}</dd>
            </dl>
            <p className="xs dim" style={{ marginBottom: 0, marginTop: "var(--space-4)" }}>
              Topology, deployments, configuration changes, metrics, logs, alerts and runbooks are gathered
              during the investigation through the MCP server, not pre-loaded here.
            </p>
          </Panel>
        </div>

        <Panel title="Start an investigation">
          <div className="stack">
            <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
              <legend className="small muted" style={{ padding: 0, marginBottom: "var(--space-2)" }}>Provider</legend>
              <div className="stack stack--tight">
                {available.map((option) => (
                  <ProviderOption
                    key={option.name}
                    option={option}
                    checked={provider === option.name}
                    onSelect={() => setProvider(option.name as ProviderName)}
                  />
                ))}
              </div>
            </fieldset>

            <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
              <legend className="small muted" style={{ padding: 0, marginBottom: "var(--space-2)" }}>Budget</legend>
              <div className="row row--wrap">
                {BUDGETS.map((option) => (
                  <label key={option.value} className="row" style={{ gap: 6, cursor: "pointer" }}>
                    <input
                      type="radio"
                      name="budget"
                      value={option.value}
                      checked={budget === option.value}
                      onChange={() => setBudget(option.value)}
                    />
                    <span className="small">{option.label}</span>
                    <span className="xs dim">{option.hint}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            {usesLive ? (
              <Banner tone="live" title="This run will call a paid vendor API">
                Model {selected?.model ?? "(not configured)"} via {PROVIDER_LABELS[provider] ?? provider}. Credentials
                come from the server environment; this page never handles them.
              </Banner>
            ) : (
              <Banner tone="info">
                No language model is used. Tool calls, hypotheses and the report come from an authored playbook,
                while the MCP boundary, evidence registry, grounding validation and audit trace are real.
              </Banner>
            )}

            {launchError && <ErrorState error={launchError} inline />}

            <button type="button" className="btn btn--primary btn--lg" onClick={launch} disabled={submitting || !canLaunch}>
              {submitting ? "Starting…" : usesLive ? "Investigate — uses live API" : "Investigate"}
            </button>
            <p className="xs dim" style={{ margin: 0 }}>
              {usesLive
                ? "USES LIVE API: YES"
                : "USES LIVE API: NO"}
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ProviderOption({ option, checked, onSelect }: {
  option: ProviderSummary;
  checked: boolean;
  onSelect: () => void;
}) {
  // `replay` needs a cassette path, which the API deliberately does not accept from a browser.
  const unavailable = option.name === "replay" || (option.uses_live_api && (!option.enabled || !option.ready));
  return (
    <label
      className="row"
      style={{
        gap: "var(--space-3)", padding: "var(--space-2) var(--space-3)", border: "1px solid",
        borderColor: checked ? "var(--accent-border)" : "var(--border-hairline)",
        background: checked ? "var(--accent-soft)" : "transparent",
        borderRadius: "var(--radius-sm)", cursor: unavailable ? "not-allowed" : "pointer",
        opacity: unavailable ? 0.62 : 1, alignItems: "flex-start",
      }}
    >
      <input
        type="radio"
        name="provider"
        value={option.name}
        checked={checked}
        disabled={unavailable}
        onChange={onSelect}
        style={{ marginTop: 3 }}
      />
      <span style={{ minWidth: 0 }}>
        <span className="row" style={{ gap: 6 }}>
          <strong className="small">{PROVIDER_LABELS[option.name] ?? option.name}</strong>
          {option.uses_live_api ? <Badge tone="live">LIVE API</Badge> : <Badge tone="ok">NO LLM</Badge>}
        </span>
        <span className="xs dim" style={{ display: "block", marginTop: 2 }}>
          {option.uses_live_api
            ? option.ready
              ? `configured on the server · model ${option.model}`
              : option.note
            : option.note}
        </span>
      </span>
    </label>
  );
}
