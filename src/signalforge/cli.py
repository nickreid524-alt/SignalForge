"""Developer CLI.

    signalforge world stats                # synthetic world statistics
    signalforge world export --out world.json
    signalforge scenarios list             # scenario catalogue (ground truth summary; not served by MCP)
    signalforge serve-mcp [--transport stdio|streamable-http] [--port 8000]
    signalforge demo [--transport in-memory|stdio] [--incident INC-2026-0101]

`demo` is an ENGINEERING DEMONSTRATION of the MCP boundary and evidence registry.
It contains no AI reasoning: the tool calls are fixed by the script.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from signalforge import __version__
from signalforge.config import DATASET_LABEL, ENVIRONMENT_NAME, WorldConfig
from signalforge.evidence.registry import EvidenceRegistry
from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.server import create_server
from signalforge.world.generator import build_snapshot
from signalforge.world.repository import WorldRepository


def _print(line: str = "") -> None:
    sys.stdout.write(line + "\n")


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
    from signalforge.scenarios.catalogue import (
        SCENARIOS,  # ground truth: never imported by the server
    )

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
