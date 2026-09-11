/** Safe system metadata. Provider rows say whether the server is configured, never with what. */

import { Badge, Banner, Loading, Metric, Panel } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { useSystem } from "@/hooks/useSystem";

export function SystemPage() {
  const { meta, health, providers, loading, error, reload } = useSystem();

  if (error) return <div className="page"><ErrorState error={error} onRetry={reload} /></div>;
  if (loading || !meta || !health) return <div className="page"><Panel title="System"><Loading rows={5} /></Panel></div>;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">System</h1>
          <p className="page__subtitle">What this SignalForge instance is running and what it is connected to.</p>
        </div>
      </div>

      <div className="stack">
        <div className="grid-4">
          <Metric label="API" value={health.status === "ok" ? "Online" : "Offline"}
            hint={`SignalForge ${health.version}`} tone={health.status === "ok" ? "ok" : "danger"} />
          <Metric label="MCP protocol" value={meta.mcp_protocol_version ?? "—"}
            hint={`${meta.mcp_server_name ?? "server"} ${meta.mcp_server_version ?? ""}`} />
          <Metric label="Synthetic incidents" value={String(meta.incident_count)} hint="open, awaiting investigation" />
          <Metric label="MCP tools" value={String(meta.tool_count)}
            hint={`${meta.resource_count} resources · ${meta.resource_template_count} templates`} />
        </div>

        <Panel title="Environment">
          <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "7px var(--space-5)", fontSize: "var(--text-base)" }}>
            <dt className="dim">Environment</dt>
            <dd style={{ margin: 0 }}>{meta.environment}</dd>
            <dt className="dim">Dataset</dt>
            <dd style={{ margin: 0 }}>
              {meta.dataset_label} <Badge tone="synthetic">SYNTHETIC</Badge>
            </dd>
            <dt className="dim">Mode</dt>
            <dd style={{ margin: 0 }}>{meta.mode}</dd>
            <dt className="dim">Default provider</dt>
            <dd className="mono" style={{ margin: 0 }}>{meta.default_provider}</dd>
            <dt className="dim">Live providers</dt>
            <dd style={{ margin: 0 }}>
              {meta.live_providers_enabled
                ? <Badge tone="live">ENABLED ON THIS SERVER</Badge>
                : <Badge tone="ok">DISABLED</Badge>}
            </dd>
            <dt className="dim">Event types</dt>
            <dd className="mono small" style={{ margin: 0 }}>{meta.event_types.length} in the stream contract</dd>
          </dl>
          <p className="xs dim" style={{ marginBottom: 0, marginTop: "var(--space-4)" }}>
            All services, deployments, logs, metrics, alerts, runbooks and incidents are generated
            deterministically from a fixed seed and describe a fictional company.
          </p>
        </Panel>

        <Panel title="Providers" flush>
          <table className="table">
            <caption className="visually-hidden">Model provider availability</caption>
            <thead>
              <tr>
                <th scope="col">Provider</th><th scope="col">Mode</th><th scope="col">Uses live API</th>
                <th scope="col">SDK</th><th scope="col">Credentials</th><th scope="col">Model</th><th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {(providers?.providers ?? meta.providers).map((provider) => (
                <tr key={provider.name}>
                  <td className="mono small" style={{ fontWeight: 600 }}>{provider.name}</td>
                  <td className="small">{provider.mode}</td>
                  <td>{provider.uses_live_api ? <Badge tone="live">YES</Badge> : <Badge tone="ok">NO</Badge>}</td>
                  <td className="small muted">
                    {provider.sdk_installed === null ? "n/a"
                      : provider.sdk_installed ? `installed ${provider.sdk_version ?? ""}` : "not installed"}
                  </td>
                  <td className="small">
                    {provider.configured === null ? <span className="dim">n/a</span>
                      : provider.configured ? <Badge tone="ok">configured</Badge> : <Badge>not configured</Badge>}
                  </td>
                  <td className="mono small">{provider.model ?? <span className="dim">—</span>}</td>
                  <td className="small muted" style={{ maxWidth: 320 }}>{provider.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Banner tone="info" title="Credentials never reach this page">
          Providers report whether the server holds a credential, never the credential itself: no value,
          prefix, length or hash is exposed by the API. Live providers are enabled by the operator at
          startup and cannot be enabled from a browser.
        </Banner>
      </div>
    </div>
  );
}
