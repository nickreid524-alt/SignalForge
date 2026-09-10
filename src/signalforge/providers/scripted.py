"""ScriptedDemoProvider: a deterministic, authored stand-in for a language model.

THIS IS NOT AN LLM. Each incident has a playbook that says which tools to call,
which hypotheses to record and what the final report says. The playbook refers to
evidence by *semantic labels*; the real EVD ids are assigned by the orchestrator's
registry at run time and resolved here from the tool results the orchestrator
sends back — exactly the information a live model would receive.

It exists so the whole pipeline (provider -> orchestrator -> MCP client -> MCP
server -> evidence registry -> grounding validator -> trace -> evaluation) can be
demonstrated and tested without any paid API.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import BaseModel

from signalforge.providers.base import (
    EVIDENCE_HEADER_PREFIX,
    Conversation,
    GenerationConfig,
    InvestigationContext,
    ModelTurn,
    ProviderError,
    ProviderInfo,
    StructuredResult,
    SystemPrompt,
    ToolRequest,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from signalforge.providers.playbooks import PLAYBOOKS, Playbook, PlaybookHypothesis
from signalforge.reports.schema import Claim, RecommendedAction, ReportDraft, ReportHypothesis

SCRIPTED_INFO = ProviderInfo(
    name="scripted-demo",
    model="playbook-v1",
    mode="scripted",
    uses_llm=False,
    description="Deterministic scripted demonstration. Tool calls, hypotheses and the report come from an authored "
                "playbook per incident; no language model is involved. The MCP boundary, evidence registry, "
                "grounding validation and audit trace are real.",
)

_REL = re.compile(r"^@clock(?:([+-])(\d+)([mhd]))?$")
_STATUS_TO_REPORT = {"supported": "supported", "refuted": "refuted", "proposed": "inconclusive", "weakened": "inconclusive"}


class _Session:
    """Per-investigation memory of what the orchestrator sent back."""

    def __init__(self) -> None:
        self.labels: dict[str, tuple[str, list[str]]] = {}   # label -> (evidence id, source ids)
        self.uris: dict[str, list[str]] = {}                   # label -> resource uris offered by that result
        self.hypothesis_ids: dict[str, str] = {}               # playbook key -> H-id
        self.latest: dict[str, PlaybookHypothesis] = {}
        self.steps_issued = 0

    def absorb(self, conversation: Conversation) -> None:
        for message in conversation:
            if isinstance(message, UserMessage):
                self._absorb_seed(message.text)
            elif isinstance(message, ToolResultsMessage):
                for result in message.results:
                    self._absorb_result(result.request_id, result.content)

    def _absorb_seed(self, text: str) -> None:
        for block in text.split(f"\n{EVIDENCE_HEADER_PREFIX}"):
            if not block.startswith(EVIDENCE_HEADER_PREFIX) and "\nsource: resource " not in block:
                continue
            header = block if block.startswith(EVIDENCE_HEADER_PREFIX) else f"{EVIDENCE_HEADER_PREFIX}{block}"
            fields = _parse_header(header)
            uri = fields.get("source", "").replace("resource ", "", 1)
            if uri.startswith("incidents://"):
                self._set("seed.queue", fields)
            elif uri.startswith("topology://"):
                self._set("seed.topology", fields)

    def _absorb_result(self, request_id: str, content: str) -> None:
        if content.startswith(EVIDENCE_HEADER_PREFIX):
            self._set(request_id, _parse_header(content))
        elif content.startswith('{"assigned"'):
            try:
                assigned = json.loads(content).get("assigned", {})
            except json.JSONDecodeError:
                return
            self.hypothesis_ids.update({k: v for k, v in assigned.items() if isinstance(v, str)})

    def _set(self, label: str, fields: dict[str, str]) -> None:
        evidence_id = fields.get("evidence_id")
        if not evidence_id:
            return
        try:
            source_ids = json.loads(fields.get("source_ids", "[]"))
        except json.JSONDecodeError:
            source_ids = []
        self.labels[label] = (evidence_id, [s for s in source_ids if isinstance(s, str)])
        if "resource_uris" in fields:
            with contextlib.suppress(json.JSONDecodeError):
                self.uris[label] = [u for u in json.loads(fields["resource_uris"]) if isinstance(u, str)]

    # ------------------------------------------------------------------ resolution
    def resolve_ref(self, ref: str) -> str | None:
        """'@label' -> EVD id; '@label#N' -> EVD id narrowed to the N-th source id."""
        if not ref.startswith("@"):
            return ref
        label, _, index = ref[1:].partition("#")
        found = self.labels.get(label)
        if found is None:
            return None
        evidence_id, source_ids = found
        if index == "":
            return evidence_id
        try:
            position = int(index)
        except ValueError:
            return evidence_id
        if 0 <= position < len(source_ids):
            return f"{evidence_id}#{source_ids[position]}"
        return evidence_id

    def resolve_refs(self, refs: list[str]) -> list[str]:
        out: list[str] = []
        for ref in refs:
            resolved = self.resolve_ref(ref)
            if resolved and resolved not in out:
                out.append(resolved)
        return out

    def resolve_uri(self, template: str) -> str | None:
        if template.startswith("@hit:"):
            _, label, index = template.split(":", 2)
            uris = self.uris.get(label, [])
            try:
                return uris[int(index)]
            except (ValueError, IndexError):
                return None
        return template


def _parse_header(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in content.splitlines():
        key, sep, value = line.partition(":")
        if not sep or " " in key.strip():
            break
        fields[key.strip()] = value.strip()
    return fields


def _resolve_scalar(value: Any, context: InvestigationContext, session: _Session) -> Any:
    if not isinstance(value, str) or not value.startswith("@"):
        return value
    if value == "@service":
        return context.affected_service
    match = _REL.match(value)
    if match:
        sign, amount, unit = match.groups()
        clock = context.investigation_clock
        if amount:
            delta = timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[unit]: int(amount)})
            clock = clock - delta if sign == "-" else clock + delta
        return clock.isoformat().replace("+00:00", "Z")
    if value.startswith("@hit:"):
        return session.resolve_uri(value)
    return value


class ScriptedDemoProvider:
    info = SCRIPTED_INFO

    def __init__(self, playbooks: Mapping[str, Playbook] | None = None) -> None:
        self.playbooks: Mapping[str, Playbook] = playbooks if playbooks is not None else PLAYBOOKS
        self._sessions: dict[str, _Session] = {}

    def _playbook(self, context: InvestigationContext) -> Playbook:
        playbook = self.playbooks.get(context.incident_id)
        if playbook is None:
            raise ProviderError(f"scripted provider has no playbook for incident {context.incident_id}")
        return playbook

    def _session(self, context: InvestigationContext, conversation: Conversation) -> _Session:
        session = self._sessions.setdefault(context.investigation_id, _Session())
        session.absorb(conversation)
        return session

    async def complete(self, system: SystemPrompt, conversation: Conversation, *, tools: list[ToolSpec],
                       context: InvestigationContext, config: GenerationConfig) -> ModelTurn:
        playbook = self._playbook(context)
        session = self._session(context, conversation)
        available = {t.name for t in tools}
        index = session.steps_issued
        if index >= len(playbook.steps):
            return ModelTurn(text="[scripted demonstration] playbook exhausted; finishing.",
                             tool_requests=[ToolRequest(id="finish", name="finish_investigation",
                                                        arguments={"reason": "playbook complete"})], stop_reason="tool_use")
        step = playbook.steps[index]
        session.steps_issued += 1
        requests: list[ToolRequest] = []
        for action in step.actions:
            if action.tool is not None:
                if action.tool not in available:
                    continue  # the catalogue changed; a scripted playbook never invents tools
                arguments = {k: _resolve_scalar(v, context, session) for k, v in action.args.items()}
                requests.append(ToolRequest(id=action.label, name=action.tool, arguments=arguments))
            elif action.uri is not None:
                uri = _resolve_scalar(action.uri, context, session)
                if isinstance(uri, str) and uri:
                    requests.append(ToolRequest(id=action.label, name="read_resource", arguments={"uri": uri}))
        if step.hypotheses:
            updates = []
            for hypothesis in step.hypotheses:
                session.latest[hypothesis.key] = hypothesis
                updates.append({
                    "ref": hypothesis.key, "id": session.hypothesis_ids.get(hypothesis.key),
                    "statement": hypothesis.statement, "confidence": hypothesis.confidence, "status": hypothesis.status,
                    "supporting_evidence_ids": session.resolve_refs(hypothesis.supporting),
                    "contradicting_evidence_ids": session.resolve_refs(hypothesis.contradicting),
                    "note": hypothesis.note,
                })
            requests.append(ToolRequest(id=f"hypotheses-{index + 1}", name="update_hypotheses", arguments={"updates": updates}))
        if step.finish:
            requests.append(ToolRequest(id="finish", name="finish_investigation",
                                        arguments={"reason": step.finish_reason or "playbook complete"}))
        labels = ", ".join(r.name if r.name != "read_resource" else "read_resource" for r in requests)
        return ModelTurn(text=f"[scripted demonstration] step {index + 1}/{len(playbook.steps)}: {labels}",
                         tool_requests=requests, stop_reason="tool_use")

    async def generate_structured(self, system: SystemPrompt, conversation: Conversation, *, schema: type[BaseModel],
                                  context: InvestigationContext, config: GenerationConfig) -> StructuredResult:
        if not issubclass(schema, ReportDraft):
            return StructuredResult(schema_name=schema.__name__, parse_error=f"scripted provider cannot produce {schema.__name__}")
        playbook = self._playbook(context)
        session = self._session(context, conversation)
        template = playbook.report
        hypotheses: list[ReportHypothesis] = []
        for key, hypothesis in session.latest.items():
            hypothesis_id = session.hypothesis_ids.get(key)
            if hypothesis_id is None:
                continue
            hypotheses.append(ReportHypothesis(
                id=hypothesis_id, statement=hypothesis.statement, category=template.hypothesis_categories.get(key, template.category),
                status=_STATUS_TO_REPORT[hypothesis.status], confidence=hypothesis.confidence,
                supporting_evidence_ids=session.resolve_refs(hypothesis.supporting),
                contradicting_evidence_ids=session.resolve_refs(hypothesis.contradicting),
                reasoning=template.reasoning.get(key, ""),
            ))
        primary = None
        if template.primary is not None:
            primary_id = session.hypothesis_ids.get(template.primary)
            primary = next((h for h in hypotheses if h.id == primary_id), None)
        draft = schema(
            summary=template.summary, status=template.status, primary_hypothesis=primary, confidence=template.confidence,
            hypotheses_considered=hypotheses,
            key_findings=[Claim(statement=c.statement, kind=c.kind, evidence_ids=session.resolve_refs(c.evidence)) for c in template.key_findings],
            contradicting_evidence=[Claim(statement=c.statement, kind=c.kind, evidence_ids=session.resolve_refs(c.evidence)) for c in template.contradicting],
            recommended_actions=[RecommendedAction(action=a.action, rationale=a.rationale, priority=a.priority, kind=a.kind,
                                                   evidence_ids=session.resolve_refs(a.evidence)) for a in template.actions],
            unknowns=[Claim(statement=c.statement, kind="UNKNOWN", evidence_ids=[]) for c in template.unknowns],
            limitations=list(template.limitations),
        )
        return StructuredResult(schema_name=schema.__name__, value=draft, raw=draft.model_dump(mode="json"))
