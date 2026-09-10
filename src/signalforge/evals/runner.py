"""Run scenarios through the real pipeline (provider -> orchestrator -> MCP -> registry -> validator) and score them."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from signalforge.audit.store import TraceStore
from signalforge.config import WorldConfig
from signalforge.evals.metrics import (
    EvaluationSummary,
    ScenarioEvaluation,
    evaluate_scenario,
    summarize,
)
from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.server import create_server
from signalforge.orchestration.budget import InvestigationBudget
from signalforge.orchestration.investigator import InvestigationResult, Investigator
from signalforge.providers.base import ModelProvider
from signalforge.providers.factory import create_provider
from signalforge.scenarios.ground_truth import GroundTruth


async def run_evaluation_async(scenario_ids: list[str] | None = None, *, provider_name: str = "scripted",
                               cassette: str | None = None, trace: TraceStore | None = None,
                               budget: InvestigationBudget | None = None, config: WorldConfig | None = None,
                               run_tag: str | None = None,
                               provider_factory=None) -> tuple[EvaluationSummary, list[InvestigationResult]]:
    gt = GroundTruth(config)
    server = create_server(snapshot=gt.repo.snapshot)
    trace = trace or TraceStore(":memory:")
    tag = run_tag or ("eval" if trace.path == ":memory:" else f"eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}")
    specs = [s for s in gt.specs() if scenario_ids is None or s.id in scenario_ids]
    evaluations: list[ScenarioEvaluation] = []
    results: list[InvestigationResult] = []
    provider_info = None
    for spec in specs:
        provider: ModelProvider = provider_factory() if provider_factory else create_provider(provider_name, cassette=cassette)
        provider_info = provider.info
        async with OpsClient.in_memory(server) as client:
            investigator = Investigator(provider, client, trace=trace, budget=budget)
            result = await investigator.run(spec.incident_id, investigation_id=f"{tag}-{spec.id.lower()}")
        results.append(result)
        evaluations.append(evaluate_scenario(spec, gt, result))
    name = provider_info.name if provider_info else provider_name
    mode = provider_info.mode if provider_info else "unknown"
    return summarize(name, mode, evaluations), results


def run_evaluation(scenario_ids: list[str] | None = None, **kwargs) -> tuple[EvaluationSummary, list[InvestigationResult]]:
    return asyncio.run(run_evaluation_async(scenario_ids, **kwargs))
