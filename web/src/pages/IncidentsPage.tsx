/** The landing screen: the open incident queue, as an on-call engineer would see it. */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getIncidents } from "@/api/client";
import { EmptyState, Loading, Panel, Severity } from "@/components/primitives";
import { ErrorState } from "@/components/ErrorState";
import { useResource } from "@/hooks/useApi";
import type { IncidentSummary } from "@/types/api";

export function IncidentsPage() {
  const { data, error, loading, reload } = useResource(() => getIncidents(), []);
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("all");
  const navigate = useNavigate();

  const severities = useMemo(
    () => Array.from(new Set((data ?? []).map((i) => i.severity))).sort(),
    [data],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data ?? []).filter((incident) => {
      if (severity !== "all" && incident.severity !== severity) return false;
      if (!needle) return true;
      return (
        incident.id.toLowerCase().includes(needle) ||
        incident.title.toLowerCase().includes(needle) ||
        incident.affected_service.toLowerCase().includes(needle)
      );
    });
  }, [data, query, severity]);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__title">Incident queue</h1>
          <p className="page__subtitle">
            Open incidents awaiting investigation. Select one to review it and start an investigation.
          </p>
        </div>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : loading ? (
        <Panel title="Open incidents"><Loading label="Loading incidents" rows={6} /></Panel>
      ) : (
        <Panel
          title={`Open incidents (${filtered.length}${filtered.length !== (data?.length ?? 0) ? ` of ${data?.length}` : ""})`}
          flush
          actions={
            <div className="row">
              <label className="visually-hidden" htmlFor="incident-search">Search incidents</label>
              <input
                id="incident-search"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search id, service, symptom"
                style={{
                  padding: "4px 9px", fontSize: "var(--text-sm)", border: "1px solid var(--border-strong)",
                  borderRadius: "var(--radius-sm)", width: 230, fontFamily: "inherit",
                }}
              />
              <label className="visually-hidden" htmlFor="incident-severity">Filter by severity</label>
              <select
                id="incident-severity"
                value={severity}
                onChange={(event) => setSeverity(event.target.value)}
                style={{
                  padding: "4px 8px", fontSize: "var(--text-sm)", border: "1px solid var(--border-strong)",
                  borderRadius: "var(--radius-sm)", background: "var(--surface)",
                }}
              >
                <option value="all">All severities</option>
                {severities.map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            </div>
          }
        >
          {filtered.length === 0 ? (
            <EmptyState title="No incidents match" inline>
              Clear the search or severity filter to see the full queue.
            </EmptyState>
          ) : (
            <div className="scroll-y">
              <table className="table table--rows-link">
                <caption className="visually-hidden">Open incidents in the synthetic operations environment</caption>
                <thead>
                  <tr>
                    <th scope="col">Incident</th>
                    <th scope="col">Severity</th>
                    <th scope="col">Affected service</th>
                    <th scope="col">Symptom</th>
                    <th scope="col">Detected</th>
                    <th scope="col">Reporter</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((incident) => (
                    <IncidentRow key={incident.id} incident={incident} onOpen={() => navigate(`/incidents/${incident.id}`)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}

function IncidentRow({ incident, onOpen }: { incident: IncidentSummary; onOpen: () => void }) {
  return (
    <tr
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <td className="table__id">
        <a
          href={`/incidents/${incident.id}`}
          onClick={(event) => { event.preventDefault(); event.stopPropagation(); onOpen(); }}
          style={{ fontWeight: 500 }}
        >
          {incident.id}
        </a>
      </td>
      <td><Severity value={incident.severity} /></td>
      <td className="mono small">{incident.affected_service}</td>
      <td style={{ maxWidth: 460 }}>{incident.title}</td>
      <td className="small muted mono">{incident.detected_at.slice(0, 16).replace("T", " ")}Z</td>
      <td className="small muted mono">{incident.reporter}</td>
    </tr>
  );
}
