"""Investigation budgets: every dimension is explicit, and exhaustion is explainable."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from signalforge.providers.base import ModelUsage


class InvestigationBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(default=8, ge=1, description="Deliberation steps (one model turn each).")
    max_tool_calls: int = Field(default=24, ge=1, description="MCP tool calls actually executed.")
    max_resource_reads: int = Field(default=12, ge=0)
    max_actions_per_step: int = Field(default=8, ge=1)
    max_model_calls: int = Field(default=14, ge=2, description="Includes report and repair calls.")
    max_repair_rounds: int = Field(default=1, ge=0)
    max_wall_clock_seconds: float = Field(default=180.0, gt=0)
    max_evidence_chars_per_result: int = Field(default=6000, ge=500,
                                               description="Rendered result size shown to the model; the registry keeps the full payload.")

    def reserved_model_calls(self) -> int:
        """Model calls held back for the report and its repairs."""
        return 1 + self.max_repair_rounds


class BudgetUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: int = 0
    tool_calls: int = 0
    resource_reads: int = 0
    model_calls: int = 0
    repair_rounds: int = 0
    suppressed_duplicates: int = 0
    rejected_actions: int = 0
    elapsed_seconds: float = 0.0
    # Token telemetry (only meaningful when the provider reports usage; scripted/replay report nothing).
    tokens_reported: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_output_tokens: int = 0

    def add_usage(self, usage: ModelUsage) -> None:
        if not usage.reported:
            return
        self.tokens_reported = True
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cached_input_tokens += usage.cached_input_tokens
        self.cache_write_tokens += usage.cache_write_tokens
        self.reasoning_output_tokens += usage.reasoning_output_tokens

    def remaining(self, budget: InvestigationBudget) -> dict[str, int]:
        return {
            "steps": max(0, budget.max_steps - self.steps),
            "tool_calls": max(0, budget.max_tool_calls - self.tool_calls),
            "resource_reads": max(0, budget.max_resource_reads - self.resource_reads),
            "model_calls": max(0, budget.max_model_calls - self.model_calls),
            "repair_rounds": max(0, budget.max_repair_rounds - self.repair_rounds),
        }


def deliberation_exhausted(budget: InvestigationBudget, usage: BudgetUsage) -> list[str]:
    """Reasons the deliberation loop must stop now (empty list = may continue)."""
    reasons: list[str] = []
    if usage.steps >= budget.max_steps:
        reasons.append(f"max_steps ({budget.max_steps}) reached")
    if usage.tool_calls >= budget.max_tool_calls:
        reasons.append(f"max_tool_calls ({budget.max_tool_calls}) reached")
    if usage.model_calls >= budget.max_model_calls - budget.reserved_model_calls():
        reasons.append(f"model calls reserved for reporting ({budget.reserved_model_calls()} of {budget.max_model_calls}) reached")
    if usage.elapsed_seconds >= budget.max_wall_clock_seconds:
        reasons.append(f"max_wall_clock_seconds ({budget.max_wall_clock_seconds:g}) reached")
    return reasons
