/** Investigations this API process has run. The server is the source; nothing is cached locally. */

import { Link } from "react-router-dom";
import { getInvestigations } from "@/api/client";
import { Badge, EmptyState, Loading, Panel } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { useResource } from "@/hooks/useApi";

export function InvestigationsPage() {
  const { data, error, loading, reload } = useResource(() => getInvestigations(), []);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Investigations</h1>
          <p className="page__subtitle">
            Investigations run by this API process. Traces persist in the server's audit store.
          </p>
        </div>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : loading ? (
        <Panel title="Investigations"><Loading rows={4} /></Panel>
      ) : (data?.length ?? 0) === 0 ? (
        <Panel title="Investigations">
          <EmptyState
            title="No investigations yet"
            action={<Link to="/incidents" className="btn btn--primary">Open the incident queue</Link>}
          >
            Pick an incident and start one. The workspace shows evidence gathering, hypothesis evolution and
            the grounded report as they happen.
          </EmptyState>
        </Panel>
      ) : (
        <Panel title={`Investigations (${data!.length})`} flush>
          <table className="table">
            <caption className="visually-hidden">Investigations run by this server</caption>
            <thead>
              <tr>
                <th scope="col">Investigation</th>
                <th scope="col">Incident</th>
                <th scope="col">Provider</th>
                <th scope="col">Status</th>
                <th scope="col">Started</th>
                <th scope="col">Finished</th>
              </tr>
            </thead>
            <tbody>
              {[...data!].reverse().map((investigation) => (
                <tr key={investigation.id}>
                  <td className="table__id">
                    <Link to={`/investigations/${investigation.id}`}>{investigation.id}</Link>
                  </td>
                  <td className="table__id">
                    <Link to={`/incidents/${investigation.incident_id}`}>{investigation.incident_id}</Link>
                  </td>
                  <td className="small">
                    <span className="row" style={{ gap: 6 }}>
                      {investigation.provider}
                      {investigation.uses_live_api
                        ? <Badge tone="live">LIVE</Badge>
                        : <Badge tone="ok">NO LLM</Badge>}
                    </span>
                  </td>
                  <td>
                    <Badge tone={
                      investigation.status === "completed" ? "ok"
                        : investigation.status.startsWith("failed") ? "danger"
                        : investigation.terminal ? "warn" : "accent"
                    }>
                      {!investigation.terminal && <span className="dot" aria-hidden="true" />}
                      {investigation.status.replace(/_/g, " ")}
                    </Badge>
                  </td>
                  <td className="small muted mono">
                    {investigation.started_at?.slice(11, 19) ?? "—"}
                  </td>
                  <td className="small muted mono">
                    {investigation.ended_at?.slice(11, 19) ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}
