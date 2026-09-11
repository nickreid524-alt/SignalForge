/**
 * TypeScript mirrors of the SignalForge HTTP contract.
 *
 * These are hand-written from the API's Pydantic response models rather than generated, because the
 * set is small and being explicit documents the contract at the boundary. `tests/contract.test.ts`
 * checks them against captured real responses so they cannot drift silently.
 */

// ---------------------------------------------------------------- system

export interface Health {
  status: "ok";
  version: string;
  uses_live_api: boolean;
}

export interface ProviderSummary {
  name: string;
  mode: "scripted" | "replay" | "live";
  uses_live_api: boolean;
  sdk_installed: boolean | null;
  sdk_version: string | null;
  /** Whether the *server* holds a credential. Never the credential, its prefix, length or hash. */
  configured: boolean | null;
  model_configured: boolean;
  model: string | null;
  ready: boolean;
  enabled: boolean;
  note: string;
}

export interface Meta {
  name: string;
  version: string;
  environment: string;
  dataset_label: string;
  mode: string;
  demonstration: boolean;
  default_provider: string;
  live_providers_enabled: boolean;
  uses_live_api: boolean;
  mcp_server_name: string | null;
  mcp_server_version: string | null;
  mcp_protocol_version: string | null;
  incident_count: number;
  tool_count: number;
  resource_count: number;
  resource_template_count: number;
  providers: ProviderSummary[];
  event_types: string[];
}

export interface ProvidersResponse {
  default: string;
  live_providers_enabled: boolean;
  note: string;
  providers: ProviderSummary[];
}

// ---------------------------------------------------------------- incidents

export interface IncidentSummary {
  id: string;
  title: string;
  severity: string;
  affected_service: string;
  detected_at: string;
  investigation_clock: string;
  reporter: string;
}

export interface IncidentDetail extends IncidentSummary {
  description: string;
}

// ---------------------------------------------------------------- MCP catalogue

export interface McpTool {
  name: string;
  title: string | null;
  description: string;
  input_schema: JsonSchema;
  read_only: boolean | null;
  idempotent: boolean | null;
}

export interface McpResource {
  uri: string;
  name: string;
  mime_type: string | null;
  description: string | null;
}

export interface McpResourceTemplate {
  uri_template: string;
  name: string;
  mime_type: string | null;
  description: string | null;
}

export interface McpToolsResponse {
  server_name: string | null;
  server_version: string | null;
  protocol_version: string | null;
  transport: string;
  read_only: boolean;
  note: string;
  tools: McpTool[];
}

export interface McpResourcesResponse {
  server_name: string | null;
  note: string;
  resources: McpResource[];
  resource_templates: McpResourceTemplate[];
}

export interface JsonSchema {
  type?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: unknown[];
  items?: JsonSchema;
  default?: unknown;
  [key: string]: unknown;
}

// ---------------------------------------------------------------- investigations

export type InvestigationStatus =
  | "queued"
  | "created"
  | "seeding"
  | "deliberating"
  | "gathering"
  | "concluding"
  | "validating"
  | "repairing"
  | "completed"
  | "completed_with_warnings"
  | "failed_validation"
  | "failed";

export type ProviderName = "scripted" | "replay" | "anthropic" | "openai";
export type BudgetProfile = "quick" | "default" | "thorough";

export interface InvestigationLinks {
  self: string;
  events: string;
  evidence: string;
  hypotheses: string;
  report: string;
  trace: string;
}

export interface InvestigationSummary {
  id: string;
  incident_id: string;
  status: InvestigationStatus;
  terminal: boolean;
  provider: string;
  provider_mode: string;
  uses_live_api: boolean;
  model: string | null;
  budget_profile: string;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface InvestigationCreated extends InvestigationSummary {
  links: InvestigationLinks;
}

export interface TokenUsage {
  /** False for providers that use no language model; the UI must say so rather than showing zeros. */
  reported: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens: number | null;
  reasoning_output_tokens: number | null;
  model_calls: number;
}

export interface BudgetUsage {
  steps: number;
  tool_calls: number;
  resource_reads: number;
  model_calls: number;
  repair_rounds: number;
  suppressed_duplicates: number;
  rejected_actions: number;
  elapsed_seconds: number;
}

export interface ValidationIssue {
  rule: string;
  severity: "error" | "warning" | "info";
  path: string;
  message: string;
}

export interface ValidationView {
  ok: boolean | null;
  errors: number;
  warnings: number;
  repair_rounds: number;
  issues: ValidationIssue[];
}

export type HypothesisStatus = "proposed" | "supported" | "weakened" | "refuted";

export interface HypothesisRevision {
  step: number;
  status: HypothesisStatus;
  confidence: number;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  note: string;
}

export interface Hypothesis {
  id: string;
  statement: string;
  status: HypothesisStatus;
  confidence: number;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  created_step: number;
  updated_step: number;
  revisions: HypothesisRevision[];
}

export interface InvestigationDetail extends InvestigationSummary {
  incident: IncidentSummary | null;
  steps: number;
  tool_calls: number;
  resource_reads: number;
  model_calls: number;
  evidence_count: number;
  rejected_actions: number;
  suppressed_duplicates: number;
  hypotheses: Hypothesis[];
  validation: ValidationView;
  token_usage: TokenUsage;
  budget: Record<string, number>;
  usage: BudgetUsage;
  budget_exhausted: boolean;
  termination_reason: string | null;
  report_available: boolean;
  error: string | null;
  links: InvestigationLinks;
}

export interface HypothesesResponse {
  investigation_id: string;
  status: InvestigationStatus;
  hypotheses: Hypothesis[];
}

// ---------------------------------------------------------------- evidence

export interface EvidenceSummary {
  evidence_id: string;
  sequence: number;
  acquired_at: string;
  source_kind: "tool" | "resource";
  source_name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  error: string | null;
  result_kind: string | null;
  source_ids: string[];
  content_hash: string;
  latency_ms: number;
  /** Always true. Evidence is retrieved content: display it as data, never as instructions. */
  untrusted: boolean;
}

export interface EvidenceDetail extends EvidenceSummary {
  payload: Record<string, unknown> | null;
  text: string | null;
}

export interface EvidenceListResponse {
  investigation_id: string;
  note: string;
  evidence: EvidenceSummary[];
}

export interface EvidenceDetailResponse {
  investigation_id: string;
  note: string;
  evidence: EvidenceDetail;
}

// ---------------------------------------------------------------- report

export type ClaimKind = "OBSERVED" | "INFERRED" | "UNKNOWN";
export type ReportStatus = "root_cause_identified" | "probable_cause" | "inconclusive";

export interface Claim {
  statement: string;
  kind: ClaimKind;
  evidence_ids: string[];
}

export interface ReportHypothesis {
  id: string;
  statement: string;
  category: string;
  status: "supported" | "refuted" | "inconclusive";
  confidence: number;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  reasoning: string;
}

export interface RecommendedAction {
  action: string;
  rationale: string;
  priority: "P1" | "P2" | "P3";
  kind: "mitigation" | "diagnostic" | "follow_up";
  evidence_ids: string[];
}

export interface EvidenceIndexEntry {
  evidence_id: string;
  source_kind: string;
  source_name: string;
  result_kind: string | null;
  record_count: number;
  ok: boolean;
  cited: boolean;
}

export interface ProviderInfo {
  name: string;
  model: string;
  mode: string;
  uses_llm: boolean;
  description: string;
}

export interface InvestigationReport {
  investigation_id: string;
  incident_id: string;
  affected_service: string;
  summary: string;
  status: ReportStatus;
  confidence: number;
  primary_hypothesis: ReportHypothesis | null;
  hypotheses_considered: ReportHypothesis[];
  key_findings: Claim[];
  contradicting_evidence: Claim[];
  recommended_actions: RecommendedAction[];
  unknowns: Claim[];
  limitations: string[];
  provider: ProviderInfo;
  terminal_status: string;
  validation: { issues: ValidationIssue[] };
  repair_rounds: number;
  steps_used: number;
  tool_calls_used: number;
  resource_reads_used: number;
  model_calls_used: number;
  suppressed_duplicate_calls: number;
  rejected_actions: number;
  investigation_duration_seconds: number;
  budget_exhausted: boolean;
  termination_reason: string | null;
  evidence_index: EvidenceIndexEntry[];
  token_usage: { reported: boolean; input_tokens: number; output_tokens: number; model_calls: number };
}

// ---------------------------------------------------------------- trace

export interface TraceStatusChange {
  at: string;
  from_status: string;
  to_status: string;
  note: string;
}

export interface TraceModelCall {
  id: string;
  step: number | null;
  purpose: string;
  provider_name: string;
  provider_model: string | null;
  latency_ms: number;
  stop_reason: string | null;
  error_category: string | null;
  usage_reported: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
}

export interface TraceAction {
  step: number;
  kind: string;
  name: string;
  arguments: Record<string, unknown>;
  accepted: boolean;
  rejection_code: string | null;
  rejection_reason: string | null;
  evidence_id: string | null;
  duplicate_of: string | null;
  ok: boolean | null;
  error: string | null;
  latency_ms: number | null;
}

export interface TraceHypothesisUpdate {
  step: number;
  hypothesis_id: string;
  statement: string;
  status: string;
  confidence: number;
  note: string;
}

export interface TraceValidation {
  round: number;
  ok: boolean;
  error_count: number;
  warning_count: number;
  issues: ValidationIssue[];
  parse_error: string | null;
}

export interface InvestigationTrace {
  investigation: {
    id: string;
    incident_id: string;
    status: string;
    provider_name: string;
    provider_model: string | null;
    provider_mode: string;
    uses_llm: boolean;
    transport: string;
    started_at: string;
    ended_at: string | null;
    termination_reason: string | null;
    error: string | null;
    budget: Record<string, number> | null;
    usage: Record<string, number> | null;
  };
  status_changes: TraceStatusChange[];
  steps: { step: number; started_at: string; ended_at: string | null; assistant_text: string | null }[];
  model_calls: TraceModelCall[];
  actions: TraceAction[];
  evidence: (EvidenceSummary & { record_count: number })[];
  hypothesis_updates: TraceHypothesisUpdate[];
  validations: TraceValidation[];
  repairs: { round: number; request_text: string }[];
  report_summary: { status: string; confidence: number; terminal_status: string } | null;
  notice: string;
}

// ---------------------------------------------------------------- evaluations

export interface BenchmarkScenario {
  scenario_id: string;
  incident_id: string;
  title: string;
  terminal_status: string;
  completed: boolean;
  expected_category: string;
  predicted_category: string | null;
  category_match: number;
  root_cause_accuracy: number;
  confidence: number | null;
  decisive_evidence_recall: number | null;
  decisive_citation_recall: number | null;
  citation_validity: number;
  unsupported_claim_rate: number;
  red_herring_adopted: boolean;
  tool_calls: number;
  tool_call_success_rate: number;
  repair_rounds: number;
  injection_resisted: boolean | null;
  calibration_ok: boolean;
  passed: boolean;
  failures: string[];
}

export interface Benchmark {
  benchmark: string;
  signalforge_version: string;
  generated_at: string;
  provider: string;
  provider_mode: string;
  uses_live_api: boolean;
  note: string;
  scenario_count: number;
  aggregates: Record<string, number>;
  scenarios: BenchmarkScenario[];
}

export interface EvaluationIndex {
  available: string[];
  note: string;
}

// ---------------------------------------------------------------- events

export const EVENT_TYPES = [
  "investigation.created",
  "status.changed",
  "step.started",
  "provider.completed",
  "tool.requested",
  "tool.completed",
  "tool.rejected",
  "resource.read",
  "evidence.registered",
  "hypothesis.updated",
  "validation.started",
  "validation.failed",
  "repair.started",
  "report.completed",
  "investigation.completed",
  "investigation.failed",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

export const TERMINAL_EVENTS: readonly EventType[] = ["investigation.completed", "investigation.failed"];

export interface EventPayloads {
  "investigation.created": {
    incident_id: string;
    incident_title: string;
    affected_service: string;
    provider: string;
    provider_mode: string;
    uses_live_api: boolean;
    model: string | null;
    budget_profile: string;
  };
  "status.changed": { from_status: string; to_status: string; note: string };
  "step.started": { step: number; budget_remaining: Record<string, number> };
  "provider.completed": {
    step: number;
    purpose: string;
    provider: string;
    model: string | null;
    stop_reason: string | null;
    latency_ms: number;
    text_preview: string;
    tool_requests: number;
    usage_reported: boolean;
    input_tokens: number | null;
    output_tokens: number | null;
  };
  "tool.requested": { step: number; request_id: string; name: string; arguments: Record<string, unknown> };
  "tool.completed": {
    step: number;
    request_id: string;
    name: string;
    evidence_id: string | null;
    ok: boolean;
    error: string | null;
    latency_ms: number;
  };
  "tool.rejected": {
    step: number;
    request_id: string;
    name: string;
    code: string;
    reason: string;
    duplicate_of: string | null;
  };
  "resource.read": {
    step: number;
    request_id: string;
    uri: string;
    evidence_id: string | null;
    ok: boolean;
    error: string | null;
  };
  "evidence.registered": {
    evidence_id: string;
    sequence: number;
    source_kind: string;
    source_name: string;
    result_kind: string | null;
    record_count: number;
    ok: boolean;
    untrusted: boolean;
  };
  "hypothesis.updated": {
    hypothesis_id: string;
    step: number;
    statement: string;
    status: HypothesisStatus;
    confidence: number;
    supporting_evidence_ids: string[];
    contradicting_evidence_ids: string[];
    note: string;
  };
  "validation.started": { round: number };
  "validation.failed": { round: number; errors: number; warnings: number; rules: string[] };
  "repair.started": { round: number; reason: string };
  "report.completed": {
    status: string;
    confidence: number;
    primary_hypothesis_id: string | null;
    validation_ok: boolean;
    repair_rounds: number;
  };
  "investigation.completed": {
    terminal_status: string;
    steps: number;
    tool_calls: number;
    resource_reads: number;
    evidence_count: number;
    rejected_actions: number;
    duration_seconds: number;
    termination_reason: string | null;
    has_report: boolean;
  };
  "investigation.failed": {
    terminal_status: string;
    message: string;
    category: string | null;
    duration_seconds: number;
  };
}

/** One event as delivered by the SSE stream. `seq` is monotonic and gap-free per investigation. */
export type InvestigationEvent = {
  [K in EventType]: {
    seq: number;
    investigation_id: string;
    type: K;
    at: string;
    payload: EventPayloads[K];
  };
}[EventType];

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: Record<string, unknown>;
  };
}
