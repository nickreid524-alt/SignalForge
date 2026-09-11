/** One place that turns an ApiError into something a person can act on. */

import { ApiError } from "@/api/client";
import { EmptyState } from "@/components/primitives";

const GUIDANCE: Record<string, { title: string; body: string }> = {
  network_error: {
    title: "The SignalForge API is not reachable",
    body: "Start the local API with `signalforge serve`, then retry. The web application talks only to that API.",
  },
  not_found: { title: "Not found", body: "This item does not exist, or it belongs to a server session that has ended." },
  report_not_ready: { title: "The report is not ready", body: "This investigation has not produced a validated report yet." },
  provider_unavailable: {
    title: "That provider is unavailable",
    body: "The server has not enabled or configured it. Providers are configured on the server, never from the browser.",
  },
  too_many_investigations: {
    title: "The runner is busy",
    body: "The server is already running its maximum number of concurrent investigations. Wait for one to finish.",
  },
  invalid_request: { title: "That request was rejected", body: "The API refused the request as invalid." },
  internal_error: { title: "The server hit an error", body: "The detail was written to the server log." },
};

export function ErrorState({ error, onRetry, inline = false }: {
  error: ApiError;
  onRetry?: () => void;
  inline?: boolean;
}) {
  const guidance = GUIDANCE[error.code] ?? { title: "Something went wrong", body: error.message };
  return (
    <EmptyState
      title={guidance.title}
      tone="error"
      inline={inline}
      action={onRetry && <button type="button" className="btn" onClick={onRetry}>Retry</button>}
    >
      <p style={{ margin: 0 }}>{guidance.body}</p>
      {error.message && error.message !== guidance.body && (
        <p className="small muted" style={{ marginTop: 8, marginBottom: 0 }}>{error.message}</p>
      )}
      {error.requestId && (
        <p className="xs dim mono" style={{ marginTop: 8, marginBottom: 0 }}>request {error.requestId}</p>
      )}
    </EmptyState>
  );
}
