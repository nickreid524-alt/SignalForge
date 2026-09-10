"""Action policy: allowlist, duplicate suppression, per-step and budget limits."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from signalforge.evidence.registry import canonical_json
from signalforge.orchestration.actions import (
    Action,
    ActionRejection,
    CallTool,
    ReadResource,
    parse_action,
)
from signalforge.providers.base import ToolRequest, ToolSpec

DEFAULT_URI_SCHEMES: tuple[str, ...] = ("catalog", "incidents", "topology", "runbook", "incident")


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    name: str
    action: Action | None = None
    rejection: ActionRejection | None = None
    duplicate_of: str | None = None

    @property
    def accepted(self) -> bool:
        return self.action is not None


def call_key(name: str, arguments: dict) -> str:
    return f"{name}:{canonical_json(arguments)}"


def resource_key(uri: str) -> str:
    return f"read_resource:{uri}"


class ActionPolicy:
    def __init__(self, tool_specs: Mapping[str, ToolSpec], *, allowed_tools: set[str] | None = None,
                 allowed_uri_schemes: tuple[str, ...] = DEFAULT_URI_SCHEMES, max_actions_per_step: int = 8) -> None:
        self.tool_specs = {name: spec for name, spec in tool_specs.items() if not spec.local}
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else set(self.tool_specs)
        unknown = self.allowed_tools - set(self.tool_specs)
        if unknown:
            raise ValueError(f"allowed_tools names tools the server does not expose: {sorted(unknown)}")
        self.allowed_uri_schemes = allowed_uri_schemes
        self.max_actions_per_step = max_actions_per_step

    def review(self, requests: list[ToolRequest], *, seen_calls: Mapping[str, str],
               remaining_tool_calls: int, remaining_resource_reads: int) -> list[PolicyDecision]:
        """Decide, in order, which requests execute. Never raises; every request gets a decision."""
        decisions: list[PolicyDecision] = []
        seen_this_step: set[str] = set()
        tool_calls = 0
        reads = 0
        for index, request in enumerate(requests):
            if index >= self.max_actions_per_step:
                decisions.append(self._reject(request, "limit_exceeded",
                                              f"more than {self.max_actions_per_step} actions in one step"))
                continue
            parsed = parse_action(request, self.tool_specs)
            if isinstance(parsed, ActionRejection):
                decisions.append(PolicyDecision(request_id=request.id, name=request.name, rejection=parsed))
                continue
            if isinstance(parsed, CallTool):
                if parsed.name not in self.allowed_tools:
                    decisions.append(self._reject(request, "tool_not_allowed",
                                                  f"{parsed.name!r} is not in this investigation's allowlist"))
                    continue
                key = call_key(parsed.name, parsed.arguments)
                if key in seen_calls or key in seen_this_step:
                    decisions.append(self._reject(request, "duplicate_call",
                                                  f"identical call already made ({seen_calls.get(key, 'this step')})",
                                                  duplicate_of=seen_calls.get(key)))
                    continue
                if tool_calls >= remaining_tool_calls:
                    decisions.append(self._reject(request, "budget_exhausted", "tool-call budget exhausted"))
                    continue
                seen_this_step.add(key)
                tool_calls += 1
            elif isinstance(parsed, ReadResource):
                scheme = parsed.uri.split("://", 1)[0] if "://" in parsed.uri else ""
                if scheme not in self.allowed_uri_schemes:
                    decisions.append(self._reject(request, "uri_not_allowed",
                                                  f"scheme {scheme or '<none>'!r} not allowed; allowed: {', '.join(self.allowed_uri_schemes)}"))
                    continue
                key = resource_key(parsed.uri)
                if key in seen_calls or key in seen_this_step:
                    decisions.append(self._reject(request, "duplicate_call", "resource already read",
                                                  duplicate_of=seen_calls.get(key)))
                    continue
                if reads >= remaining_resource_reads:
                    decisions.append(self._reject(request, "budget_exhausted", "resource-read budget exhausted"))
                    continue
                seen_this_step.add(key)
                reads += 1
            decisions.append(PolicyDecision(request_id=request.id, name=request.name, action=parsed))
        return decisions

    @staticmethod
    def _reject(request: ToolRequest, code: str, reason: str, duplicate_of: str | None = None) -> PolicyDecision:
        return PolicyDecision(
            request_id=request.id, name=request.name,
            rejection=ActionRejection(request_id=request.id, name=request.name, code=code, reason=reason),  # type: ignore[arg-type]
            duplicate_of=duplicate_of,
        )
