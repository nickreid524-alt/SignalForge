"""Developer CLI.

    signalforge world stats | world export --out world.json
    signalforge scenarios list
    signalforge serve-mcp [--transport stdio|streamable-http] [--port 8000]
    signalforge demo [--transport in-memory|stdio] [--incident INC-2026-0101]
    signalforge providers                                   # what each provider needs and whether it is ready
    signalforge provider check anthropic|openai --yes       # one tiny LIVE call to verify credentials (paid)
    signalforge investigate INC-2026-0101 [--provider scripted|replay|anthropic|openai] [--yes] [--json trace.json]
    signalforge trace <investigation-id> [--json out.json] | trace --list
    signalforge eval [--scenario SCN-01] [--provider ...] [--yes] [--allow-live-suite] [--json out.json]

The default provider is `scripted` (SCRIPTED DEMONSTRATION MODE - no LLM API). Live providers call PAID
APIs and require --yes; a multi-scenario live evaluation additionally requires --allow-live-suite.
Every command states USES LIVE API: YES / NO for the selected provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio

from signalforge import __version__
from signalforge.audit.export import export_investigation, write_export
from signalforge.audit.store import TraceStore
from signalforge.config import DATASET_LABEL, ENVIRONMENT_NAME, WorldConfig
from signalforge.evidence.registry import EvidenceRegistry
from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.server import create_server
from signalforge.orchestration.budget import InvestigationBudget
from signalforge.orchestration.investigator import Investigator
from signalforge.providers.base import (
    GenerationConfig,
    InvestigationContext,
    ProviderError,
    ProviderInfo,
    SystemPrompt,
    UserMessage,
)
from signalforge.providers.errors import ProviderFailure
from signalforge.providers.factory import (
    LIVE_PROVIDERS,
    PROVIDER_CHOICES,
    ProviderSettings,
    all_provider_statuses,
    create_provider,
    default_provider_name,
    provider_status,
)
from signalforge.reports.render import render_markdown
from signalforge.world.generator import build_snapshot
from signalforge.world.repository import WorldRepository

DEFAULT_TRACE_DB = "runs/signalforge.sqlite"


def _print(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _mode_banner(info: ProviderInfo) -> None:
    _print("=" * 78)
    if info.mode == "scripted":
        _print("SCRIPTED DEMONSTRATION MODE")
        _print("No LLM API is being used. Tool calls, hypotheses and the report come from an authored playbook.")
        _print("The MCP boundary, evidence registry, grounding validation and audit trace are real.")
    elif info.mode == "replay":
        _print("REPLAY MODE")
        _print("No LLM API is being used. Recorded provider interactions are replayed deterministically.")
    else:
        _print(f"LIVE MODE: provider {info.name}, model {info.model}")
        _print("This run calls a PAID vendor API with the key from your environment.")
    _print(f"USES LIVE API: {'YES' if info.uses_live_api else 'NO'}")
    _print("=" * 78)


def _live_guard(provider_name: str, yes: bool, *, what: str) -> int | None:
    """For live providers: refuse to proceed without --yes. Returns an exit code to stop with, or None."""
    if provider_name not in LIVE_PROVIDERS:
        return None
    status = provider_status(provider_name)
    if not yes:
        _print("=" * 78)
        _print(f"LIVE API CONFIRMATION REQUIRED: {what} would call the {provider_name} API (paid).")
        _print(f"  model: {status.model or '(not configured)'}   ready: {'yes' if status.ready else 'no - ' + status.note}")
        _print("  Re-run with --yes to confirm. Nothing was called.")
        _print("=" * 78)
        return 2
    if not status.ready:
        _print(f"setup error: provider {provider_name} is not ready ({status.note}). Nothing was called.")
        return 2
    return None


def _print_tokens(report_or_usage: Any) -> None:
    usage = report_or_usage
    if not getattr(usage, "reported", getattr(usage, "tokens_reported", False)):
        _print("TOKENS    not reported (provider uses no LLM)")
        return
    _print(f"TOKENS    input={usage.input_tokens} output={usage.output_tokens} cached_input={usage.cached_input_tokens} "
           f"reasoning_output={usage.reasoning_output_tokens}")


# ---------------------------------------------------------------------- world / scenarios / serve / demo (Phase 1)


def cmd_world_stats(args: argparse.Namespace) -> int:
    snapshot = build_snapshot(WorldConfig(seed=args.seed))
    repo = WorldRepository(snapshot)
    _print(f"{ENVIRONMENT_NAME} - {DATASET_LABEL} (seed {snapshot.seed})")
    _print(f"span: {snapshot.reference_start.date()} -> {snapshot.reference_end.date()}")
    rows = {
        "services": len(snapshot.services), "infrastructure nodes": len(snapshot.nodes),
        "dependency edges": len(snapshot.edges), "deployments": len(snapshot.deployments),
        "configuration changes": len(snapshot.config_changes), "alerts": len(snapshot.alerts),
        "open incidents": len(snapshot.open_incidents), "runbooks": len(snapshot.runbooks),
        "historical incidents": len(snapshot.historical_incidents), "metric effects": len(snapshot.metric_effects),
        "log effects": len(snapshot.log_effects), "status effects": len(snapshot.status_effects),
    }
    for key, value in rows.items():
        _print(f"  {key:<24} {value}")
    _print("open incidents:")
    for inc in repo.open_incidents():
        _print(f"  {inc.id}  {inc.detected_at:%Y-%m-%d %H:%M}Z  {inc.severity}  {inc.affected_service:<19} {inc.title}")
    return 0


def cmd_world_export(args: argparse.Namespace) -> int:
    snapshot = build_snapshot(WorldConfig(seed=args.seed))
    out = Path(args.out)
    out.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    _print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


def cmd_scenarios_list(args: argparse.Namespace) -> int:
    from signalforge.scenarios.catalogue import SCENARIOS  # ground truth: evaluation side only

    _print("scenario   incident       category                 difficulty  visible service      culprit")
    for s in SCENARIOS:
        _print(f"{s.id:<10} {s.incident_id:<14} {s.category:<24} {s.difficulty:<11} {s.visible_service:<20} {s.culprit_location}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    server = create_server(WorldConfig(seed=args.seed))
    if args.transport == "stdio":
        server.run()
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


async def _demo(args: argparse.Namespace) -> int:
    config = WorldConfig(seed=args.seed)
    _print("=" * 78)
    _print("SignalForge Phase 1 - ENGINEERING DEMONSTRATION (no AI reasoning involved)")
    _print("Exercises: world generation -> MCP server -> MCP client -> tools/resources -> evidence registry")
    _print("=" * 78)
    _print("\n[1] World generation")
    snapshot = build_snapshot(config)
    repo = WorldRepository(snapshot)
    _print(f"    {snapshot.environment} | {snapshot.dataset_label} | seed={snapshot.seed}")
    _print(f"    {len(snapshot.services)} services, {len(snapshot.nodes)} infra nodes, {len(snapshot.edges)} edges, "
           f"{len(snapshot.deployments)} deployments, {len(snapshot.config_changes)} config changes, "
           f"{len(snapshot.alerts)} alerts, {len(snapshot.runbooks)} runbooks, "
           f"{len(snapshot.historical_incidents)} historical incidents")
    incident = repo.open_incident(args.incident)
    if incident is None:
        _print(f"    unknown incident {args.incident}; known: {', '.join(i.id for i in repo.open_incidents())}")
        return 2
    _print(f"\n[2] Incident under investigation: {incident.id} - {incident.title}")
    _print(f"    affected service: {incident.affected_service} | detected {incident.detected_at.isoformat()} | "
           f"investigation clock {incident.investigation_clock.isoformat()}")
    _print(f"\n[3] MCP server startup + client connection (transport: {args.transport})")
    if args.transport == "stdio":
        cm = OpsClient.stdio(args=["-m", "signalforge.mcp_server", "--transport", "stdio", "--seed", str(args.seed)])
    else:
        cm = OpsClient.in_memory(create_server(config))
    async with cm as client:
        _print(f"    connected via {client.transport}: server={client.server_name} v{client.server_version} "
               f"protocol={client.protocol_version}")
        _print("\n[4] tools/list")
        tools = await client.list_tools()
        for tool in tools:
            out_props = len((tool.output_schema or {}).get("properties", {}))
            _print(f"    - {tool.name:<20} read_only={tool.read_only} output_schema_fields={out_props}")
        templates = await client.list_resource_templates()
        statics = await client.list_resources()
        _print(f"    resources: {[r.uri for r in statics]} templates: {[t.uri_template for t in templates]}")
        registry = EvidenceRegistry(investigation_id=f"demo-{incident.id}")
        clock = incident.investigation_clock
        window_start = (clock - timedelta(hours=3)).isoformat()
        window_end = clock.isoformat()
        _print(f"\n[5] Query the incident-related service ({incident.affected_service}) through MCP tools")
        calls: list[tuple[str, dict[str, Any]]] = [
            ("get_service_health", {"node": incident.affected_service, "as_of": clock.isoformat()}),
            ("get_deployments", {"service": incident.affected_service, "start": window_start, "end": window_end}),
            ("query_metrics", {"node": incident.affected_service, "metric": "latency_p95", "start": window_start,
                               "end": window_end, "step_seconds": 300}),
            ("query_logs", {"node": incident.affected_service, "start": window_start, "end": window_end,
                            "level": "WARN", "limit": 5}),
        ]
        for name, arguments in calls:
            item = await client.gather(registry, name, arguments)
            status = "ok" if item.ok else f"ERROR {item.error}"
            summary = (item.payload or {}).get("summary", "") if item.ok else ""
            _print(f"    {item.evidence_id}  {name:<20} {item.latency_ms:7.1f} ms  {status}")
            if summary:
                _print(f"               {summary[:150]}")
        _print("\n[6] Search runbooks (lexical BM25 over SQLite FTS5)")
        search = await client.gather(registry, "search_runbooks",
                                     {"query": f"{incident.affected_service} {incident.title}", "limit": 3})
        hits = (search.payload or {}).get("hits", [])
        _print(f"    {search.evidence_id}  search_runbooks      {search.latency_ms:7.1f} ms  {len(hits)} hit(s)")
        for hit in hits:
            _print(f"               #{hit['rank']} {hit['source_id']} | {hit['title']} / {hit['section']} -> {hit['resource_uri']}")
        _print("\n[7] Read the top returned resource")
        if hits:
            doc = await client.read_as_evidence(registry, hits[0]["resource_uri"])
            first_line = (doc.text or "").splitlines()[0] if doc.text else ""
            _print(f"    {doc.evidence_id}  {hits[0]['resource_uri']:<22} {doc.latency_ms:7.1f} ms  "
                   f"{len(doc.text or '')} chars  {first_line}")
        topo = await client.read_as_evidence(registry, f"topology://services/{incident.affected_service}")
        _print(f"    {topo.evidence_id}  topology resource     {topo.latency_ms:7.1f} ms  source_ids={topo.source_ids[:4]}...")
        _print("\n[8] Evidence registry (investigation-local EVD ids over stable world ids)")
        for item in registry.items():
            _print(f"    {item.evidence_id}  seq={item.sequence}  {item.source_kind:<8} {item.source_name:<38} "
                   f"records={len(item.source_ids):<3} hash={item.content_hash[7:19]}...  ok={item.ok}")
        example = next((i for i in registry.items() if i.source_ids), None)
        if example:
            _print(f"    narrowed citation example: {example.evidence_id}#{example.source_ids[0]}")
    _print("\nDone. Every result above crossed a real MCP client/server boundary "
           f"({args.transport}); nothing was scripted as 'reasoning'.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    return asyncio.run(_demo(args))


# ---------------------------------------------------------------------- providers (Phase 3)


def cmd_providers(args: argparse.Namespace) -> int:
    _print(f"{'provider':<10} {'mode':<9} {'USES LIVE API':<14} {'sdk':<18} {'credentials':<12} {'model':<28} {'ready':<6} note")
    for status in all_provider_statuses(ProviderSettings.from_env()):
        sdk = "n/a" if status.sdk_installed is None else (f"installed {status.sdk_version}" if status.sdk_installed else "not installed")
        creds = "n/a" if status.credentials_present is None else ("present" if status.credentials_present else "missing")
        _print(f"{status.name:<10} {status.mode:<9} {('YES' if status.uses_live_api else 'NO'):<14} {sdk:<18} {creds:<12} "
               f"{(status.model or '-'):<28} {('yes' if status.ready else 'no'):<6} {status.note}")
    _print("")
    _print(f"default provider: {default_provider_name()} (SIGNALFORGE_PROVIDER). Live providers need an API key from the vendor's")
    _print("developer console: a Claude consumer subscription is not an Anthropic API key, and a ChatGPT subscription is not an OpenAI API key.")
    return 0


async def _provider_check(args: argparse.Namespace) -> int:
    name = args.provider
    status = provider_status(name)
    if not status.uses_live_api:
        _print(f"{name}: USES LIVE API: NO - nothing to check ({status.note}).")
        return 0
    if not status.ready:
        _print(f"setup error: provider {name} is not ready ({status.note}). Nothing was called.")
        return 2
    guard = _live_guard(name, args.yes, what="provider check")
    if guard is not None:
        return guard
    try:
        provider = create_provider(name)
    except ProviderFailure as exc:
        _print(f"setup error [{exc.category}]: {exc.detail}")
        return 2
    _mode_banner(provider.info)
    _print(f"one request, max_output_tokens=32, timeout={args.timeout:g}s, no tools")
    context = InvestigationContext(investigation_id="provider-check", incident_id="none", affected_service="none",
                                   investigation_clock=datetime.now(UTC), step=0, purpose="deliberate")
    config = GenerationConfig(max_output_tokens=32, timeout_seconds=args.timeout)
    try:
        with anyio.fail_after(args.timeout + 5):
            turn = await provider.complete(SystemPrompt(text="You are a connectivity check. Reply with the single word OK."),
                                           [UserMessage(text="Reply with the single word OK.")], tools=[], context=context,
                                           config=config)
    except ProviderFailure as exc:
        _print(f"FAILED [{exc.category}] {exc.detail}")
        return 1
    except TimeoutError:
        _print(f"FAILED [timeout] no response within {args.timeout:g}s")
        return 1
    _print(f"OK  model={provider.info.model} latency={turn.latency_ms:.0f}ms text={(turn.text or '')[:60]!r}")
    _print_tokens(turn.usage)
    return 0


def cmd_provider_check(args: argparse.Namespace) -> int:
    return asyncio.run(_provider_check(args))


# ---------------------------------------------------------------------- investigate / trace / eval


def _budget_from_args(args: argparse.Namespace) -> InvestigationBudget:
    overrides = {k: v for k, v in {
        "max_steps": getattr(args, "max_steps", None), "max_tool_calls": getattr(args, "max_tool_calls", None),
        "max_model_calls": getattr(args, "max_model_calls", None),
        "max_repair_rounds": getattr(args, "max_repair_rounds", None),
        "max_wall_clock_seconds": getattr(args, "max_seconds", None),
    }.items() if v is not None}
    return InvestigationBudget(**overrides)


async def _investigate(args: argparse.Namespace) -> int:
    guard = _live_guard(args.provider, args.yes, what=f"investigate {args.incident}")
    if guard is not None:
        return guard
    try:
        provider = create_provider(args.provider, cassette=args.cassette)
    except ProviderFailure as exc:
        _print(f"setup error [{exc.category}]: {exc.detail}")
        return 2
    except ProviderError as exc:
        _print(f"error: {exc}")
        return 2
    _mode_banner(provider.info)
    trace = TraceStore(args.trace_db)
    budget = _budget_from_args(args)
    if provider.info.uses_live_api:
        _print(f"budget: max_steps={budget.max_steps} max_tool_calls={budget.max_tool_calls} max_model_calls={budget.max_model_calls} "
               f"max_repair_rounds={budget.max_repair_rounds} max_wall_clock={budget.max_wall_clock_seconds:g}s")
    config = WorldConfig(seed=args.seed)
    if args.transport == "stdio":
        cm = OpsClient.stdio(args=["-m", "signalforge.mcp_server", "--transport", "stdio", "--seed", str(args.seed)])
    else:
        cm = OpsClient.in_memory(create_server(config))
    async with cm as client:
        _print(f"MCP: connected via {client.transport} to {client.server_name} v{client.server_version} "
               f"(protocol {client.protocol_version})")
        investigator = Investigator(provider, client, trace=trace, budget=budget)
        result = await investigator.run(args.incident)
    state = result.state
    incident = state.incident
    _print(f"\nINCIDENT  {state.incident_id}" + (f" - {incident.title}" if incident else ""))
    if incident:
        _print(f"          service={incident.affected_service} severity={incident.severity} "
               f"clock={incident.investigation_clock.isoformat()}")
    _print(f"STATUS    {state.status.value}" + (f"  ({state.error})" if state.error else ""))
    if state.termination_reason:
        _print(f"BUDGET    exhausted: {state.termination_reason}")
    _print("\nSTEPS")
    for step in state.steps:
        _print(f"  step {step.step}: {(step.assistant_text or '')[:160]}")
        for action in step.actions:
            if action.accepted:
                target = action.arguments.get("uri") if action.kind == "read_resource" else action.name
                extra = f" -> {action.evidence_id}" if action.evidence_id else ""
                flag = "" if action.ok in (True, None) else f"  ERROR {action.error}"
                _print(f"      {action.kind:<20} {target}{extra}{flag}")
            else:
                dup = f" (reuse {action.duplicate_of})" if action.duplicate_of else ""
                _print(f"      REJECTED {action.name}: {action.rejection_code}{dup}")
    _print("\nEVIDENCE")
    for item in result.registry.items():
        _print(f"  {item.evidence_id}  {item.source_kind:<8} {item.source_name:<44} records={len(item.source_ids):<3} ok={item.ok}")
    _print("\nHYPOTHESES (evolution)")
    for hypothesis in state.hypotheses.hypotheses:
        trail = " -> ".join(f"s{r.step}:{r.status}@{r.confidence:.2f}" for r in hypothesis.revisions)
        _print(f"  {hypothesis.id} [{hypothesis.status:<9} {hypothesis.confidence:.2f}] {hypothesis.statement[:110]}")
        _print(f"      {trail}")
    report = result.report
    _print("\nCONCLUSION")
    if report is None:
        _print("  no report produced")
    else:
        primary = report.primary_hypothesis
        _print(f"  status: {report.status}   confidence: {report.confidence:.2f}")
        if primary:
            _print(f"  primary: {primary.id} [{primary.category}] {primary.statement}")
            _print(f"  cites:   {', '.join(primary.supporting_evidence_ids)}")
        _print(f"  summary: {report.summary}")
        validation = report.validation
        _print(f"\nVALIDATION  {'ok' if validation.ok else 'FAILED'}  errors={len(validation.errors)} "
               f"warnings={len(validation.warnings)} repair_rounds={report.repair_rounds}")
        for issue in validation.issues:
            _print(f"  {issue.severity.upper()} [{issue.rule}] {issue.path}: {issue.message}")
    _print(f"\nUSAGE     steps={state.usage.steps} tool_calls={state.usage.tool_calls} resource_reads={state.usage.resource_reads} "
           f"model_calls={state.usage.model_calls} duplicates_suppressed={state.usage.suppressed_duplicates} "
           f"rejected={state.usage.rejected_actions} elapsed={state.usage.elapsed_seconds:.1f}s")
    _print_tokens(state.usage)
    _print(f"PROVIDER  {provider.info.name} ({provider.info.mode}) USES LIVE API: {'YES' if provider.info.uses_live_api else 'NO'}")
    _print(f"TRACE ID  {state.investigation_id}  (db: {args.trace_db})")
    if args.markdown and report is not None:
        _print("\n" + render_markdown(report))
    if args.json:
        out = write_export(trace, state.investigation_id, args.json)
        _print(f"trace exported to {out}")
    return 0 if state.status.value in ("completed", "completed_with_warnings") else 1


def cmd_investigate(args: argparse.Namespace) -> int:
    return asyncio.run(_investigate(args))


def cmd_trace(args: argparse.Namespace) -> int:
    store = TraceStore(args.trace_db)
    if args.list:
        for row in store.list_investigations():
            _print(f"{row['id']:<40} {row['incident_id']:<15} {row['provider_name']!s:<16} {row['status']:<24} {row['started_at']}")
        return 0
    if not args.investigation_id:
        _print("error: an investigation id is required (or use --list)")
        return 2
    bundle = store.load(args.investigation_id)
    if bundle is None:
        _print(f"error: no investigation {args.investigation_id!r} in {args.trace_db}")
        return 2
    inv = bundle["investigation"]
    _print(f"INVESTIGATION {inv['id']}  incident={inv['incident_id']}  provider={inv['provider_name']} ({inv['provider_mode']}, "
           f"uses_llm={bool(inv['uses_llm'])})  transport={inv['transport']}")
    _print(f"  status={inv['status']}  started={inv['started_at']}  ended={inv['ended_at']}  "
           f"termination={inv['termination_reason']}  error={inv['error']}")
    _print(f"  usage={inv['usage']}")
    _print("\nSTATUS CHANGES")
    for change in bundle["status_changes"]:
        _print(f"  {change['at']}  {change['from_status']} -> {change['to_status']}  {change['note']}")
    _print("\nMODEL CALLS")
    for call in bundle["model_calls"]:
        tokens = (f"tokens in={call['input_tokens']} out={call['output_tokens']}" if call.get("usage_reported") else "tokens n/a")
        _print(f"  {call['id']}  step={call['step']}  purpose={call['purpose']}  model={call.get('provider_model')}  "
               f"latency={call['latency_ms']}ms  {tokens}  stop={call['stop_reason']}  error={call['error_category'] or call['error']}")
    _print("\nACTIONS")
    for action in bundle["actions"]:
        if action["accepted"]:
            _print(f"  step {action['step']}: {action['kind']:<20} {action['name']:<22} -> {action['evidence_id'] or '-'}  "
                   f"ok={action['ok']}  args={json.dumps(action['arguments'])[:90]}")
        else:
            _print(f"  step {action['step']}: REJECTED {action['name']} ({action['rejection_code']}: {action['rejection_reason']})")
    _print("\nEVIDENCE INSPECTED")
    for item in bundle["evidence"]:
        _print(f"  {item['evidence_id']}  {item['source_kind']:<8} {item['source_name']:<44} records={len(item['source_ids'] or [])}  ok={bool(item['ok'])}")
    _print("\nHYPOTHESIS UPDATES")
    for update in bundle["hypothesis_updates"]:
        _print(f"  step {update['step']}: {update['hypothesis_id']} {update['status']:<9} {update['confidence']:.2f} {update['statement'][:100]}")
    _print("\nVALIDATION ROUNDS")
    for round_ in bundle["validations"]:
        _print(f"  round {round_['round']}: ok={bool(round_['ok'])} errors={round_['error_count']} warnings={round_['warning_count']}")
        for issue in round_["issues"] or []:
            _print(f"      {issue['severity'].upper()} [{issue['rule']}] {issue['path']}: {issue['message']}")
    for repair in bundle["repairs"]:
        _print(f"  repair request {repair['round']}: {repair['request_text'][:160]}")
    report = bundle["report"]
    if report:
        _print(f"\nREPORT  status={report['status']} confidence={report['confidence']} terminal={report['terminal_status']}")
        primary = report.get("primary_hypothesis")
        if primary:
            _print(f"  primary {primary['id']} [{primary['category']}]: {primary['statement']}")
    export = export_investigation(store, args.investigation_id)
    summary = export["inspection_summary"]
    _print(f"\nINSPECTED BEFORE CONCLUDING: {len(summary['evidence_gathered'])} evidence items, "
           f"{len(summary['evidence_cited_in_report'])} cited, {len(summary['evidence_gathered_but_uncited'])} uncited, "
           f"{len(summary['suppressed_or_rejected'])} suppressed/rejected actions")
    if args.json:
        out = write_export(store, args.investigation_id, args.json)
        _print(f"trace exported to {out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from signalforge.evals.report import comparable, render_table
    from signalforge.evals.report import render_markdown as render_eval_markdown
    from signalforge.evals.runner import run_evaluation

    scenario_ids = [args.scenario] if args.scenario else None
    if args.provider in LIVE_PROVIDERS:
        guard = _live_guard(args.provider, args.yes, what=f"eval ({args.scenario or 'all 15 scenarios'})")
        if guard is not None:
            return guard
        if scenario_ids is None and not args.allow_live_suite:
            _print("LIVE SUITE GUARD: evaluating all 15 scenarios against a paid API requires --allow-live-suite "
                   "(or pick one with --scenario SCN-xx). Nothing was called.")
            return 2
    try:
        info = create_provider(args.provider, cassette=args.cassette).info
    except ProviderFailure as exc:
        _print(f"setup error [{exc.category}]: {exc.detail}")
        return 2
    except ProviderError as exc:
        _print(f"error: {exc}")
        return 2
    _mode_banner(info)
    trace = TraceStore(args.trace_db) if args.trace_db else None
    summary, _ = run_evaluation(scenario_ids, provider_name=args.provider, cassette=args.cassette, trace=trace,
                                budget=_budget_from_args(args))
    _print(render_table(summary))
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(comparable(summary), indent=2), encoding="utf-8")
        _print(f"\nwrote {out}")
    if args.markdown:
        out = Path(args.markdown)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_eval_markdown(summary), encoding="utf-8")
        _print(f"wrote {out}")
    return 0 if summary.aggregates["passed"] == summary.aggregates["scenarios"] else 1


# ---------------------------------------------------------------------- parser


def _add_budget_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-tool-calls", type=int)
    parser.add_argument("--max-model-calls", type=int)
    parser.add_argument("--max-repair-rounds", type=int)
    parser.add_argument("--max-seconds", type=float)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signalforge", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"signalforge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    world = sub.add_parser("world", help="synthetic world commands")
    world_sub = world.add_subparsers(dest="world_command", required=True)
    stats = world_sub.add_parser("stats", help="print world statistics")
    stats.add_argument("--seed", type=int, default=WorldConfig().seed)
    stats.set_defaults(func=cmd_world_stats)
    export = world_sub.add_parser("export", help="export the server-facing snapshot as JSON")
    export.add_argument("--out", default="world.json")
    export.add_argument("--seed", type=int, default=WorldConfig().seed)
    export.set_defaults(func=cmd_world_export)

    scenarios = sub.add_parser("scenarios", help="scenario catalogue (evaluation ground truth)")
    scenarios_sub = scenarios.add_subparsers(dest="scenarios_command", required=True)
    listing = scenarios_sub.add_parser("list")
    listing.set_defaults(func=cmd_scenarios_list)

    serve = sub.add_parser("serve-mcp", help="run the Synthetic Operations MCP Server")
    serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--seed", type=int, default=WorldConfig().seed)
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser("demo", help="engineering demonstration of the MCP boundary (no AI)")
    demo.add_argument("--transport", choices=["in-memory", "stdio"], default="in-memory")
    demo.add_argument("--incident", default="INC-2026-0101")
    demo.add_argument("--seed", type=int, default=WorldConfig().seed)
    demo.set_defaults(func=cmd_demo)

    providers = sub.add_parser("providers", help="list providers, whether they use a live API, and their readiness")
    providers.set_defaults(func=cmd_providers)

    provider = sub.add_parser("provider", help="provider utilities")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    check = provider_sub.add_parser("check", help="make ONE tiny live call to verify credentials (paid API; requires --yes)")
    check.add_argument("provider", choices=PROVIDER_CHOICES)
    check.add_argument("--yes", action="store_true", help="confirm that a real API call may be made")
    check.add_argument("--timeout", type=float, default=60.0)
    check.set_defaults(func=cmd_provider_check)

    investigate = sub.add_parser("investigate", help="run a bounded investigation of an open incident")
    investigate.add_argument("incident", help="open incident id, e.g. INC-2026-0101")
    investigate.add_argument("--provider", choices=PROVIDER_CHOICES, default=default_provider_name())
    investigate.add_argument("--cassette", help="recorded cassette path (replay provider)")
    investigate.add_argument("--yes", action="store_true", help="confirm a LIVE (paid) provider run")
    investigate.add_argument("--transport", choices=["in-memory", "stdio"], default="in-memory")
    investigate.add_argument("--trace-db", default=DEFAULT_TRACE_DB)
    investigate.add_argument("--seed", type=int, default=WorldConfig().seed)
    _add_budget_flags(investigate)
    investigate.add_argument("--markdown", action="store_true", help="also print the rendered report")
    investigate.add_argument("--json", help="export the investigation trace to this JSON file")
    investigate.set_defaults(func=cmd_investigate)

    trace = sub.add_parser("trace", help="inspect a persisted investigation trace")
    trace.add_argument("investigation_id", nargs="?")
    trace.add_argument("--trace-db", default=DEFAULT_TRACE_DB)
    trace.add_argument("--list", action="store_true", help="list investigations in the trace database")
    trace.add_argument("--json", help="export the trace to this JSON file")
    trace.set_defaults(func=cmd_trace)

    evaluate = sub.add_parser("eval", help="run the deterministic evaluation harness")
    evaluate.add_argument("--scenario", help="run a single scenario, e.g. SCN-01")
    evaluate.add_argument("--provider", choices=PROVIDER_CHOICES, default=default_provider_name())
    evaluate.add_argument("--cassette")
    evaluate.add_argument("--yes", action="store_true", help="confirm a LIVE (paid) provider run")
    evaluate.add_argument("--allow-live-suite", action="store_true",
                          help="allow a multi-scenario evaluation against a live provider (many paid calls)")
    evaluate.add_argument("--trace-db", help="persist investigation traces to this SQLite file (default: in-memory)")
    evaluate.add_argument("--json", help="write the comparable evaluation summary to this JSON file")
    evaluate.add_argument("--markdown", help="write a Markdown results table to this file")
    _add_budget_flags(evaluate)
    evaluate.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
