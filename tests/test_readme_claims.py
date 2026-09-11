"""The README is a claim document, so its numbers are checked like any other assertion.

Every figure the README states is re-derived here from the code, the world and the frozen benchmark.
If a tool is added, a scenario changes or the benchmark is regenerated, this fails and the README has
to be corrected rather than quietly becoming untrue.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from signalforge.api.app import routes
from signalforge.events.models import EventType
from signalforge.mcp_client.client import OpsClient
from signalforge.providers.factory import PROVIDER_CHOICES

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
BENCHMARK = json.loads((ROOT / "src" / "signalforge" / "api" / "data" / "scripted_benchmark.json")
                       .read_text(encoding="utf-8"))
AGGREGATES = BENCHMARK["aggregates"]

pytestmark = pytest.mark.anyio


def claim(text: str) -> None:
    assert text in README, f"README no longer contains: {text!r}"


# ---------------------------------------------------------------------- counts from the code


async def test_mcp_counts_match_the_server(server):
    async with OpsClient.in_memory(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        protocol = client.protocol_version

    assert len(tools) == 9
    claim("9 read-only MCP tools")
    claim("**9 read-only tools**")
    assert (len(resources), len(templates)) == (2, 3)
    claim("**2 resources and 3 resource templates**")
    assert protocol == "2026-07-28"
    claim("protocol `2026-07-28`")
    assert all(tool.read_only for tool in tools), "the README calls every tool read-only"


def test_provider_and_endpoint_counts():
    assert len(PROVIDER_CHOICES) == 4
    claim("4 interchangeable providers")
    assert len(routes()) == 18
    claim("18 endpoints")
    assert len(list(EventType)) == 16


def test_world_counts_match_the_generated_world(generated):
    snapshot = generated.snapshot
    for count, text in (
        (len(snapshot.services), "12 services"),
        (len(snapshot.nodes), "12 infrastructure nodes"),
        (len(snapshot.edges), "29 dependency edges"),
        (len(snapshot.deployments), "136 deployments"),
        (len(snapshot.runbooks), "22 runbooks"),
        (len(snapshot.historical_incidents), "12 historical incident reviews"),
        (len(snapshot.open_incidents), "15 open incidents"),
    ):
        stated = int(re.match(r"(\d+)", text).group(1))
        assert count == stated, f"README says {text!r} but the world has {count}"
        claim(text)


def test_scenario_count():
    from signalforge.scenarios.catalogue import SCENARIOS  # evaluation side of the firewall

    assert len(SCENARIOS) == 15 == BENCHMARK["scenario_count"]
    claim("15 synthetic incident scenarios")


# ---------------------------------------------------------------------- benchmark table


def test_every_benchmark_figure_in_the_readme_is_the_measured_one():
    rows = {
        "| Scenarios passed | 15 / 15 |": AGGREGATES["passed"] == 15.0,
        "| Root-cause category accuracy | 100% |": AGGREGATES["root_cause_accuracy_mean"] == 1.0,
        "| Citation validity | 100% |": AGGREGATES["citation_validity_mean"] == 1.0,
        "| Unsupported claims | 0% |": AGGREGATES["unsupported_claim_rate_mean"] == 0.0,
        "| Decisive evidence recall | 96.8% |": round(AGGREGATES["decisive_evidence_recall_mean"] * 100, 1) == 96.8,
        "| Decisive citation recall | 93.6% |": round(AGGREGATES["decisive_citation_recall_mean"] * 100, 1) == 93.6,
        "| Red herrings adopted as cause | 0 of 15 |": AGGREGATES["red_herring_adoption_rate"] == 0.0,
        "| Prompt-injection attempts resisted | 3 of 3 |":
            AGGREGATES["injection_resisted_rate"] == 1.0 and AGGREGATES["injection_scenarios"] == 3.0,
        "| MCP tool calls | 154 (10.3 per scenario) |":
            AGGREGATES["tool_calls_total"] == 154.0 and round(AGGREGATES["tool_calls_mean"], 1) == 10.3,
        "| Repair rounds needed | 0 |": AGGREGATES["repair_rounds_total"] == 0.0,
        "| Calibration, including the unanswerable scenario | 15 / 15 |": AGGREGATES["calibration_ok_rate"] == 1.0,
    }
    for row, measured in rows.items():
        claim(row)
        assert measured, f"the measured value behind {row!r} has changed"


def test_dependency_versions_are_current():
    from importlib import metadata

    assert metadata.version("mcp") == "2.2.0"
    claim("`mcp` 2.2.0")
    for package, text in (("anthropic", "`anthropic` 1.5.0"), ("openai", "`openai` 3.12.0")):
        try:
            version = metadata.version(package)
        except metadata.PackageNotFoundError:
            pytest.skip(f"{package} is an optional extra and is not installed")
        assert text.endswith(version), f"README states {text!r}, installed is {version}"
        claim(text)


def test_test_counts_are_stated_accurately():
    backend = sum(1 for _ in (ROOT / "tests").glob("test_*.py"))
    assert backend == 32, f"README says 32 backend test files, found {backend}"
    claim("Backend (pytest, 32 files) | 342")
    claim("342 backend tests")
    frontend = sum(1 for _ in (ROOT / "web" / "tests").glob("*.test.ts*"))
    assert frontend == 5, f"README says 5 frontend test files, found {frontend}"
    claim("Frontend (Vitest, 5 files) | 54")
    claim("54 frontend tests")


# ---------------------------------------------------------------------- honesty


def test_the_readme_does_not_overclaim():
    """The scripted provider is not a model. The README must not let a reader think otherwise."""
    for phrase in ("perfect accuracy", "state of the art", "state-of-the-art", "100% accurate",
                   "human-level", "outperforms", "best-in-class", "beats GPT", "beats Claude"):
        assert phrase.lower() not in README.lower(), phrase

    # Architecture correctness and live model quality are separated explicitly.
    claim("They measure the *architecture*, not a language model")
    claim("explicitly not an LLM")
    claim("`uses_llm: false`")
    claim("Live model quality is a separate question")

    # Subscriptions are not API credentials.
    claim("A Claude subscription is not Anthropic API access, and a ChatGPT subscription is not OpenAI API")

    # The data is stated as synthetic.
    claim("Everything in this repository is generated")
    claim("No production system, customer, employer or real incident is represented here")


def test_every_screenshot_the_readme_references_exists():
    referenced = re.findall(r"!\[[^\]]*\]\((docs/screenshots/[^)]+)\)", README)
    assert len(referenced) == 4, f"expected four inline screenshots, found {len(referenced)}"
    for relative in referenced:
        path = ROOT / relative
        assert path.exists(), f"README references a missing screenshot: {relative}"
        assert path.stat().st_size > 10_000, relative

    shipped = sorted(p.name for p in (ROOT / "docs" / "screenshots").glob("*.png"))
    assert shipped == ["evaluations.png", "grounded-report.png", "incident-queue.png",
                       "investigation-workspace.png", "mcp-catalogue.png"]


def test_mermaid_diagrams_are_well_formed():
    """Structural guard on the two README diagrams.

    Both were verified against the real Mermaid parser during Phase 6. This keeps the properties
    that would break GitHub's rendering if someone edited them: balanced delimiters, a declared
    direction, and no stray parenthesis inside a rectangular label. ``[(...)]`` is the cylinder
    shape and is expected.
    """
    blocks = re.findall(r"```mermaid\n(.*?)```", README, re.S)
    assert len(blocks) == 2, f"expected two Mermaid diagrams, found {len(blocks)}"
    for block in blocks:
        first = block.strip().splitlines()[0].strip()
        assert re.fullmatch(r"flowchart (LR|TB|TD|RL)", first), first
        assert block.count("[") == block.count("]"), "unbalanced node brackets"
        assert block.count("{") == block.count("}"), "unbalanced decision braces"
        assert block.count("(") == block.count(")"), "unbalanced parentheses"
        # A plain `[label]` must not contain a parenthesis; `[(label)]` is the cylinder shape.
        for line in block.splitlines():
            for label in re.findall(r"\[(?!\()([^\[\]]*)\]", line):
                assert "(" not in label and ")" not in label, f"parenthesis in a plain label: {line}"
