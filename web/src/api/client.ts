/**
 * The single place this application talks to SignalForge.
 *
 * Components never call fetch. They call these functions, and every failure arrives as an
 * `ApiError` carrying the server's stable error code, so screens can branch on
 * `report_not_ready` or `provider_unavailable` instead of parsing messages.
 */

import type {
  Benchmark,
  EvaluationIndex,
  EvidenceDetailResponse,
  EvidenceListResponse,
  Health,
  HypothesesResponse,
  IncidentDetail,
  IncidentSummary,
  InvestigationCreated,
  InvestigationDetail,
  InvestigationReport,
  InvestigationSummary,
  InvestigationTrace,
  McpResourcesResponse,
  McpToolsResponse,
  Meta,
  ProvidersResponse,
} from "@/types/api";
import type { ApiErrorBody, BudgetProfile, ProviderName } from "@/types/api";

/** Same-origin by default: Vite proxies /api to the local API, so no CORS origin is needed. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export type ApiErrorCode =
  | "not_found"
  | "invalid_request"
  | "provider_unavailable"
  | "investigation_conflict"
  | "report_not_ready"
  | "too_many_investigations"
  | "method_not_allowed"
  | "internal_error"
  | "network_error";

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;

  constructor(code: ApiErrorCode, message: string, status: number, requestId: string | null = null,
              details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.details = details;
  }

  /** True when the API could not be reached at all, as opposed to answering with an error. */
  get isOffline(): boolean {
    return this.code === "network_error";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    });
  } catch (cause) {
    throw new ApiError("network_error",
      "Cannot reach the SignalForge API. Is `signalforge serve` running?", 0, null, { cause: String(cause) });
  }

  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  let body: ApiErrorBody | null = null;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    body = null;
  }
  const error = body?.error;
  throw new ApiError(
    (error?.code as ApiErrorCode) ?? "internal_error",
    error?.message ?? `The API returned HTTP ${response.status}.`,
    response.status,
    error?.request_id ?? null,
    error?.details ?? {},
  );
}

// ---------------------------------------------------------------- system

export const getHealth = () => request<Health>("/api/health");
export const getMeta = () => request<Meta>("/api/meta");
export const getProviders = () => request<ProvidersResponse>("/api/providers");

// ---------------------------------------------------------------- incidents

export const getIncidents = () =>
  request<{ incidents: IncidentSummary[] }>("/api/incidents").then((r) => r.incidents);
export const getIncident = (incidentId: string) => request<IncidentDetail>(`/api/incidents/${incidentId}`);

// ---------------------------------------------------------------- MCP catalogue

export const getMcpTools = () => request<McpToolsResponse>("/api/mcp/tools");
export const getMcpResources = () => request<McpResourcesResponse>("/api/mcp/resources");

// ---------------------------------------------------------------- investigations

export interface CreateInvestigationInput {
  incident_id: string;
  provider?: ProviderName;
  budget_profile?: BudgetProfile;
}

/**
 * Start an investigation. The body carries an incident, a provider name and a budget profile and
 * nothing else: the API rejects unknown fields, and credentials, models, prompts and URLs are the
 * server's business, never the browser's.
 */
export const createInvestigation = (input: CreateInvestigationInput) =>
  request<InvestigationCreated>("/api/investigations", { method: "POST", body: JSON.stringify(input) });

export const getInvestigations = () =>
  request<{ investigations: InvestigationSummary[] }>("/api/investigations").then((r) => r.investigations);
export const getInvestigation = (id: string) => request<InvestigationDetail>(`/api/investigations/${id}`);
export const getHypotheses = (id: string) => request<HypothesesResponse>(`/api/investigations/${id}/hypotheses`);
export const getEvidenceList = (id: string) => request<EvidenceListResponse>(`/api/investigations/${id}/evidence`);
export const getEvidence = (id: string, evidenceId: string) =>
  request<EvidenceDetailResponse>(`/api/investigations/${id}/evidence/${evidenceId}`);
export const getReport = (id: string) => request<InvestigationReport>(`/api/investigations/${id}/report`);
export const getTrace = (id: string) => request<InvestigationTrace>(`/api/investigations/${id}/trace`);

// ---------------------------------------------------------------- evaluations

export const getEvaluations = () => request<EvaluationIndex>("/api/evaluations");
export const getBenchmark = (name = "scripted") => request<Benchmark>(`/api/evaluations/${name}`);

export const eventStreamUrl = (id: string) => `${API_BASE}/api/investigations/${id}/events`;
