/**
 * The MCP catalogue.
 *
 * This page exists to show that SignalForge speaks to a real Model Context Protocol server: the
 * tools, their published input schemas and their read-only annotations all come from the server's
 * own metadata. There is deliberately no execute button; tool use belongs to an investigation.
 */

import { getMcpResources, getMcpTools } from "@/api/client";
import { Badge, Banner, Collapsible, JsonBlock, Loading, Panel } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { useResource } from "@/hooks/useApi";
import type { JsonSchema } from "@/types/api";

export function McpPage() {
  const tools = useResource(() => getMcpTools(), []);
  const resources = useResource(() => getMcpResources(), []);

  if (tools.error) return <div className="page"><ErrorState error={tools.error} onRetry={tools.reload} /></div>;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">MCP operations server</h1>
          <p className="page__subtitle">
            The read-only Model Context Protocol server that supplies every piece of evidence in an investigation.
          </p>
        </div>
      </div>

      <div className="stack">
        <Panel title="Server">
          {tools.loading || !tools.data ? <Loading rows={2} /> : (
            <div className="row row--wrap" style={{ gap: "var(--space-4)" }}>
              <Fact label="Server" value={tools.data.server_name ?? "—"} />
              <Fact label="Version" value={tools.data.server_version ?? "—"} />
              <Fact label="Protocol" value={tools.data.protocol_version ?? "—"} />
              <Fact label="Transport" value={tools.data.transport} />
              <Fact label="Tools" value={String(tools.data.tools.length)} />
              <Fact label="Access" value={<Badge tone="ok">READ ONLY</Badge>} />
            </div>
          )}
        </Panel>

        <Banner tone="info">
          {tools.data?.note ??
            "Catalogue only. Tools are executed by the investigation orchestrator, behind the action policy, the budget and the audit trace."}
        </Banner>

        <Panel title={`Tools (${tools.data?.tools.length ?? 0})`} flush>
          {tools.loading ? <Loading rows={5} /> : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {tools.data?.tools.map((tool) => (
                <li key={tool.name} style={{ padding: "var(--space-3) var(--space-4)", borderBottom: "1px solid var(--border-hairline)" }}>
                  <div className="row row--wrap" style={{ gap: 8, marginBottom: 4 }}>
                    <span className="mono" style={{ fontWeight: 600, fontSize: "var(--text-md)" }}>{tool.name}</span>
                    {tool.read_only && <Badge tone="ok">read-only</Badge>}
                    {tool.idempotent && <Badge>idempotent</Badge>}
                  </div>
                  <p className="small muted" style={{ margin: "0 0 4px", maxWidth: "80ch", lineHeight: 1.55 }}>
                    {tool.description}
                  </p>
                  <SchemaParams schema={tool.input_schema} />
                  <Collapsible summary="JSON input schema">
                    <JsonBlock value={tool.input_schema} maxHeight={260} />
                  </Collapsible>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Resources" flush>
          {resources.loading || !resources.data ? <Loading rows={3} /> : (
            <table className="table">
              <thead>
                <tr><th scope="col">URI</th><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Description</th></tr>
              </thead>
              <tbody>
                {resources.data.resources.map((resource) => (
                  <tr key={resource.uri}>
                    <td className="mono small">{resource.uri}</td>
                    <td className="small">{resource.name}</td>
                    <td><Badge>static</Badge></td>
                    <td className="small muted">{resource.description ?? "—"}</td>
                  </tr>
                ))}
                {resources.data.resource_templates.map((template) => (
                  <tr key={template.uri_template}>
                    <td className="mono small">{template.uri_template}</td>
                    <td className="small">{template.name}</td>
                    <td><Badge tone="accent">template</Badge></td>
                    <td className="small muted">{template.description ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="xs dim" style={{ textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>{label}</div>
      <div className="mono" style={{ fontSize: "var(--text-md)", marginTop: 2 }}>{value}</div>
    </div>
  );
}

/** A readable parameter list, so the schema is legible without expanding the raw JSON. */
function SchemaParams({ schema }: { schema: JsonSchema }) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const names = Object.keys(properties);
  if (names.length === 0) return null;
  return (
    <div className="row row--wrap" style={{ gap: 6, marginBottom: 2 }}>
      {names.map((name) => {
        const property = properties[name];
        return (
          <span
            key={name}
            className="mono xs"
            title={property?.description ?? undefined}
            style={{
              border: "1px solid var(--border)", background: "var(--surface-sunken)",
              borderRadius: 3, padding: "0 5px", color: "var(--fg-muted)",
            }}
          >
            {name}
            <span className="dim">:{String(property?.type ?? "any")}</span>
            {required.has(name) && <span style={{ color: "var(--danger)" }}>*</span>}
          </span>
        );
      })}
    </div>
  );
}
