"""Authored playbooks for the ScriptedDemoProvider - one per open incident.

A playbook is a script, not intelligence. It names tools and arguments (with
times relative to the incident's investigation clock), records hypotheses, and
builds the final report from *labels* such as ``@deploys#0`` that the provider
resolves to real evidence ids at run time. Playbooks were authored with
knowledge of the scenarios, like a runbook written by someone who has seen the
incident before; at run time they never consult the scenario ground truth.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from signalforge.reports.taxonomy import CauseCategory


class PlaybookModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaybookAction(PlaybookModel):
    label: str
    tool: str | None = None
    uri: str | None = None
    args: dict[str, Any] = {}


class PlaybookHypothesis(PlaybookModel):
    key: str
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["proposed", "supported", "weakened", "refuted"] = "proposed"
    supporting: list[str] = []
    contradicting: list[str] = []
    note: str = ""


class PlaybookStep(PlaybookModel):
    actions: list[PlaybookAction] = []
    hypotheses: list[PlaybookHypothesis] = []
    finish: bool = False
    finish_reason: str = ""


class ClaimTemplate(PlaybookModel):
    statement: str
    kind: Literal["OBSERVED", "INFERRED", "UNKNOWN"] = "OBSERVED"
    evidence: list[str] = []


class ActionTemplate(PlaybookModel):
    action: str
    rationale: str
    priority: Literal["P1", "P2", "P3"] = "P2"
    kind: Literal["mitigation", "diagnostic", "follow_up"] = "mitigation"
    evidence: list[str] = []


class ReportTemplate(PlaybookModel):
    status: Literal["root_cause_identified", "probable_cause", "inconclusive"]
    confidence: float = Field(ge=0.0, le=1.0)
    category: CauseCategory
    primary: str | None
    summary: str
    hypothesis_categories: dict[str, CauseCategory] = {}
    reasoning: dict[str, str] = {}
    key_findings: list[ClaimTemplate]
    contradicting: list[ClaimTemplate] = []
    actions: list[ActionTemplate] = []
    unknowns: list[ClaimTemplate] = []
    limitations: list[str] = []


class Playbook(PlaybookModel):
    scenario_id: str
    incident_id: str
    title: str
    steps: list[PlaybookStep] = Field(min_length=1)
    report: ReportTemplate


# ------------------------------------------------------------------ authoring helpers


def call(label: str, tool: str, **args: Any) -> PlaybookAction:
    return PlaybookAction(label=label, tool=tool, args=args)


def read(label: str, uri: str) -> PlaybookAction:
    return PlaybookAction(label=label, uri=uri)


def hyp(key: str, statement: str, confidence: float, status: str = "proposed", *, supporting: list[str] | None = None,
        contradicting: list[str] | None = None, note: str = "") -> PlaybookHypothesis:
    return PlaybookHypothesis(key=key, statement=statement, confidence=confidence, status=status,  # type: ignore[arg-type]
                              supporting=supporting or [], contradicting=contradicting or [], note=note)


def step(*actions: PlaybookAction, hypotheses: list[PlaybookHypothesis] | None = None, finish: bool = False,
         finish_reason: str = "") -> PlaybookStep:
    return PlaybookStep(actions=list(actions), hypotheses=hypotheses or [], finish=finish, finish_reason=finish_reason)


def observed(statement: str, *evidence: str) -> ClaimTemplate:
    return ClaimTemplate(statement=statement, kind="OBSERVED", evidence=list(evidence))


def inferred(statement: str, *evidence: str) -> ClaimTemplate:
    return ClaimTemplate(statement=statement, kind="INFERRED", evidence=list(evidence))


def unknown(statement: str) -> ClaimTemplate:
    return ClaimTemplate(statement=statement, kind="UNKNOWN")


def act(action: str, rationale: str, *evidence: str, priority: str = "P2", kind: str = "mitigation") -> ActionTemplate:
    return ActionTemplate(action=action, rationale=rationale, priority=priority, kind=kind, evidence=list(evidence))  # type: ignore[arg-type]


# Common opening moves: health, recent deployments/config/alerts around the incident.
def _triage(service: str = "@service", window: str = "@clock-6h") -> list[PlaybookAction]:
    return [
        call("health", "get_service_health", node=service, as_of="@clock"),
        call("deploys", "get_deployments", service=service, start=window, end="@clock"),
        call("configs", "get_config_changes", node=service, start=window, end="@clock"),
        call("alerts", "get_alerts", start=window, end="@clock"),
    ]


PLAYBOOK_LIST: list[Playbook] = [
    # ------------------------------------------------------------------ SCN-01 checkout deployment regression
    Playbook(
        scenario_id="SCN-01", incident_id="INC-2026-0101", title="Checkout latency after deployment",
        steps=[
            step(*_triage(), hypotheses=[
                hyp("h_deploy", "The 08:09 checkout release changed reservation behaviour and slowed order submission", 0.5,
                    supporting=["@deploys#0"]),
                hyp("h_payvault", "PayVault latency is slowing checkout through payments", 0.3,
                    supporting=["@alerts"], note="a PayVault latency alert fired at 08:20"),
            ]),
            step(
                call("p95", "query_metrics", node="@service", metric="latency_p95", start="@clock-3h", end="@clock"),
                call("dep_inventory", "query_metrics", node="@service", metric="dependency_latency_p95",
                     dimension="dependency=inventory", start="@clock-3h", end="@clock"),
                call("dep_payments", "query_metrics", node="@service", metric="dependency_latency_p95",
                     dimension="dependency=payments", start="@clock-3h", end="@clock"),
                call("logs", "query_logs", node="@service", start="@clock-1h", end="@clock", level="WARN", limit=40),
                hypotheses=[
                    hyp("h_deploy", "Checkout release v2026.08.03-1 set reservation.batch_mode=false, fanning out one inventory "
                        "reserve call per line item and multiplying checkout latency", 0.85, "supported",
                        supporting=["@deploys#0", "@p95", "@dep_inventory", "@logs"]),
                    hyp("h_payvault", "PayVault latency is slowing checkout through payments", 0.05, "refuted",
                        contradicting=["@dep_payments"], note="payments dependency latency is flat"),
                ]),
            step(
                call("inventory_health", "get_service_health", node="inventory", as_of="@clock"),
                call("inventory_rr", "query_metrics", node="inventory", metric="request_rate", start="@clock-3h", end="@clock"),
                call("rb_search", "search_runbooks", query="checkout latency regression rollback release", service="@service", limit=3),
            ),
            step(read("runbook", "@hit:rb_search:0"), finish=True,
                 finish_reason="deployment aligned with onset, mechanism visible in logs, dependency latency isolated to inventory"),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.86, category="deployment_regression", primary="h_deploy",
            hypothesis_categories={"h_payvault": "third_party_degradation"},
            reasoning={"h_deploy": "Onset at 08:11 follows the 08:09-08:11 deployment; change notes name the batch_mode default; "
                                   "logs show per-item reserve fan-out; only the inventory dependency latency rose."},
            summary="Checkout p95 rose from ~180 ms to ~2.8 s two minutes after release v2026.08.03-1 finished deploying. "
                    "The release defaulted reservation.batch_mode to false, so each order issues one inventory reserve call per "
                    "line item; checkout->inventory dependency latency and inventory request rate rose in step while payments "
                    "and PayVault stayed flat. Rolling back the checkout release is the recommended mitigation.",
            key_findings=[
                observed("Checkout is degraded at the investigation clock with p95 far above its typical value", "@health"),
                observed("Exactly one checkout deployment (v2026.08.03-1) landed in the six hours before onset, finishing at 08:11",
                         "@deploys#0"),
                observed("Checkout p95 latency steps up by more than an order of magnitude at 08:11-08:15", "@p95"),
                observed("Checkout->inventory dependency latency rose sharply at the same time", "@dep_inventory"),
                observed("Checkout logs show per-line-item inventory reserve fan-out with batch_mode=false", "@logs"),
                observed("Inventory request rate multiplied while inventory itself stayed healthy", "@inventory_rr", "@inventory_health"),
                inferred("The release changed reservation fan-out, which is sufficient to explain the latency multiplier",
                         "@deploys#0", "@logs", "@dep_inventory"),
            ],
            contradicting=[
                observed("A PayVault latency warning fired at 08:20 but resolved within minutes", "@alerts"),
                observed("Checkout->payments dependency latency remained flat through the incident", "@dep_payments"),
            ],
            actions=[
                act("Roll back checkout to the previous release", "Onset matches the deployment and the runbook describes a safe rollback",
                    "@deploys#0", "@runbook", priority="P1"),
                act("Re-ship the reservation refactor only with reservation.batch_mode=true or the batch reserve API",
                    "The fan-out is the mechanism visible in the logs", "@logs", priority="P2", kind="follow_up"),
            ],
            unknowns=[unknown("Whether the batch reserve API on inventory is ready for production")],
            limitations=["Synthetic environment: no direct view of code diffs, only deployment change notes"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-02 orders pool exhaustion (reporting misrouted)
    Playbook(
        scenario_id="SCN-02", incident_id="INC-2026-0102", title="Orders 503s from pool exhaustion",
        steps=[
            step(*_triage(), hypotheses=[
                hyp("h_deploy", "The 11:05 orders release leaked or held database connections", 0.4, supporting=["@deploys#0"]),
                hyp("h_pool", "The orders connection pool is exhausted by something outside orders", 0.35, supporting=["@health"]),
            ]),
            step(
                call("pool_active", "query_metrics", node="@service", metric="db_pool_active", start="@clock-3h", end="@clock"),
                call("pool_max", "query_metrics", node="@service", metric="db_pool_max", start="@clock-3h", end="@clock"),
                call("errors", "query_logs", node="@service", start="@clock-1h", end="@clock", level="ERROR", limit=40),
                call("deps", "get_dependencies", service="@service", direction="downstream"),
            ),
            step(
                call("db_reporting", "query_metrics", node="orders-db", metric="connections_by_client", dimension="client=reporting",
                     start="@clock-3h", end="@clock"),
                call("db_orders", "query_metrics", node="orders-db", metric="connections_by_client", dimension="client=orders",
                     start="@clock-3h", end="@clock"),
                call("all_configs", "get_config_changes", start="@clock-3h", end="@clock"),
                call("db_logs", "query_logs", node="orders-db", start="@clock-1h", end="@clock", level="WARN", limit=20),
                hypotheses=[
                    hyp("h_pool", "Reporting was repointed from the orders-db replica to the primary at 13:40; its long analytical "
                        "queries hold primary connections and starve the orders pool", 0.84, "supported",
                        supporting=["@all_configs", "@db_reporting", "@pool_active", "@errors", "@db_logs"]),
                    hyp("h_deploy", "The 11:05 orders release leaked or held database connections", 0.08, "refuted",
                        contradicting=["@pool_active", "@deploys#0"],
                        note="pool usage was flat for 2.5 hours after the release and jumped at 13:45"),
                ]),
            step(call("rb_search", "search_runbooks", query="database connection pool exhausted competing client primary replica", limit=3)),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.84, category="db_pool_exhaustion", primary="h_pool",
            hypothesis_categories={"h_deploy": "deployment_regression"},
            reasoning={"h_pool": "Pool saturation starts within five minutes of the reporting db_host change; the database shows "
                                 "reporting connections on the primary; orders' own release was stable for hours."},
            summary="Orders returns 503s because its orders-db connection pool is pinned at its maximum. The primary is being "
                    "held by reporting, which was repointed from the replica to the primary at 13:40 to work around replica lag. "
                    "The 11:05 orders release is unrelated: pool usage stayed flat until 13:45.",
            key_findings=[
                observed("Orders is degraded with an elevated error rate at the investigation clock", "@health"),
                observed("Orders db_pool_active climbs to db_pool_max shortly after 13:45", "@pool_active", "@pool_max"),
                observed("Orders logs show connection acquisition timeouts against a full pool", "@errors"),
                observed("orders-db shows reporting connections appearing on the primary from ~13:42", "@db_reporting"),
                observed("A configuration change repointed reporting.db_host from orders-db-replica to orders-db-primary at 13:40",
                         "@all_configs"),
                observed("orders-db logs long-running queries from the reporting client", "@db_logs"),
                inferred("Reporting's analytical queries on the primary starve the orders pool", "@all_configs", "@db_reporting", "@pool_active"),
            ],
            contradicting=[
                observed("An orders deployment landed at 11:05, three hours before onset, with flat pool usage afterwards",
                         "@deploys#0", "@pool_active"),
            ],
            actions=[
                act("Point reporting back at the orders-db replica and terminate its long-running primary queries",
                    "Removes the competing client; no orders change is needed", "@all_configs", "@db_reporting", priority="P1"),
                act("Add a database-side guard preventing batch clients from connecting to the primary",
                    "Prevents recurrence of misrouted analytics", "@all_configs", priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("Why the replica was lagging enough to motivate the repoint")],
            limitations=["Connection counts are sampled per client; individual query plans are not visible"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-03 catalog memory leak
    Playbook(
        scenario_id="SCN-03", incident_id="INC-2026-0103", title="Catalog OOM restarts",
        steps=[
            step(*_triage(), hypotheses=[
                hyp("h_traffic", "Campaign traffic pushed catalog past its memory limit", 0.35, supporting=["@alerts"]),
                hyp("h_leak", "Catalog leaks memory between restarts (sawtooth), from a change before today", 0.4, supporting=["@health"]),
            ]),
            step(
                call("memory", "query_metrics", node="@service", metric="memory_usage", start="@clock-3h", end="@clock"),
                call("restarts", "query_metrics", node="@service", metric="restart_count", start="@clock-3h", end="@clock"),
                call("rr", "query_metrics", node="@service", metric="request_rate", start="@clock-3h", end="@clock"),
                call("errors", "query_logs", node="@service", start="@clock-3h", end="@clock", level="ERROR", limit=20),
                call("warns", "query_logs", node="@service", start="@clock-1h", end="@clock", level="WARN", limit=20),
            ),
            step(
                call("deploys_72h", "get_deployments", service="@service", start="@clock-72h", end="@clock-6h"),
                call("inc_search", "search_incidents", query="catalog OOM memory restarts leak", limit=3),
                call("rb_search", "search_runbooks", query="catalog memory pressure OOM cache unbounded", limit=3),
                hypotheses=[
                    hyp("h_leak", "Catalog release v2026.08.05-2 introduced an in-process cache with cache.max_entries=0 (unbounded); "
                        "the heap grows until the container is OOM-killed roughly every 40 minutes", 0.82, "supported",
                        supporting=["@deploys_72h#0", "@memory", "@restarts", "@errors", "@warns"]),
                    hyp("h_traffic", "Campaign traffic pushed catalog past its memory limit", 0.1, "weakened",
                        contradicting=["@rr", "@memory"],
                        note="the sawtooth period is stable and predates the 09:00 traffic increase"),
                ]),
            step(read("runbook", "@hit:rb_search:0"), read("past_incident", "@hit:inc_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.82, category="memory_leak", primary="h_leak",
            hypothesis_categories={"h_traffic": "traffic_spike"},
            reasoning={"h_leak": "The memory sawtooth and OOMKilled restarts begin after the 2026-08-05 release that added an "
                                 "unbounded cache; traffic rose 15% only this morning and cannot produce a periodic sawtooth."},
            summary="Catalog pods are OOM-killed about every 40 minutes. Memory shows a steady sawtooth ending in each restart, "
                    "and GC pauses report a growing product cache. The catalog release of 2026-08-05 (v2026.08.05-2) introduced "
                    "an in-process cache with an unbounded default (cache.max_entries=0). Today's 15% traffic increase is a "
                    "coincidence, not a cause.",
            key_findings=[
                observed("Catalog is degraded with restarts in the last hour", "@health"),
                observed("Catalog memory follows a sawtooth that resets at each restart", "@memory", "@restarts"),
                observed("Catalog logs contain OOMKilled events and GC pauses with growing cache entry counts", "@errors", "@warns"),
                observed("A catalog release two days earlier introduced an in-process product cache with an unbounded default",
                         "@deploys_72h#0"),
                inferred("An unbounded in-process cache explains a leak proportional to traffic with a stable period",
                         "@deploys_72h#0", "@memory", "@warns"),
            ],
            contradicting=[
                observed("Request rate rose about 15% from 09:00 (campaign), after the restarts had already begun", "@rr", "@alerts"),
            ],
            actions=[
                act("Roll back catalog to the release before v2026.08.05-2 or set a bounded cache.max_entries and redeploy",
                    "Removes the unbounded cache", "@deploys_72h#0", "@runbook", priority="P1"),
                act("Do not raise the memory limit as a fix; it only lengthens the restart period", "Runbook guidance for sawtooth leaks",
                    "@runbook", priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("The exact heap composition (no heap dump is available through the tools)")],
            limitations=["The culprit deployment lies outside a naive same-day window; a 72-hour lookback was required"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-04 reporting disk saturation
    Playbook(
        scenario_id="SCN-04", incident_id="INC-2026-0104", title="Reporting disk full",
        steps=[
            step(*_triage(), hypotheses=[
                hyp("h_disk", "The reporting node ran out of disk and the ETL cannot write", 0.5, supporting=["@health"]),
                hyp("h_bus", "Event-bus consumer lag for reporting-etl is blocking the job", 0.25, supporting=["@alerts"]),
            ]),
            step(
                call("disk", "query_metrics", node="@service", metric="disk_usage", start="@clock-72h", end="@clock", step_seconds=3600),
                call("errors", "query_logs", node="@service", start="@clock-2h", end="@clock", level="ERROR", limit=20),
                call("old_configs", "get_config_changes", node="@service", start="@clock-7d", end="@clock-4d"),
                hypotheses=[
                    hyp("h_disk", "logrotate.enabled was set to false on reporting on 2026-08-04; logs filled the disk over six days "
                        "and the ETL now fails with ENOSPC", 0.82, "supported",
                        supporting=["@old_configs", "@disk", "@errors"]),
                    hyp("h_bus", "Event-bus consumer lag for reporting-etl is blocking the job", 0.05, "refuted",
                        contradicting=["@errors", "@disk"], note="lag is a consequence of the job not running"),
                ]),
            step(call("rb_search", "search_runbooks", query="disk full logrotate batch node no space left", limit=3)),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.82, category="disk_saturation", primary="h_disk",
            hypothesis_categories={"h_bus": "queue_backlog"},
            reasoning={"h_disk": "Disk usage climbs linearly from the day rotation was disabled to 98%; ETL errors are ENOSPC; "
                                 "the consumer-lag alert follows the failure rather than preceding it."},
            summary="The nightly reports failed because the reporting node's disk is at 98% and the ETL writes fail with "
                    "'No space left on device'. Disk usage has climbed steadily since a 2026-08-04 configuration change disabled "
                    "log rotation (logrotate.enabled=false). The event-bus consumer-lag alert is a consequence of the job not running.",
            key_findings=[
                observed("Reporting is degraded with disk usage near full", "@health", "@disk"),
                observed("ETL log lines report 'No space left on device' on the reporting log volume", "@errors"),
                observed("Disk usage rises linearly over the 72-hour window rather than stepping", "@disk"),
                observed("A configuration change on 2026-08-04 set logrotate.enabled=false with a debugging rationale", "@old_configs"),
                inferred("Unrotated logs filled the disk over six days", "@old_configs", "@disk", "@errors"),
            ],
            contradicting=[observed("An event-bus consumer-lag alert for reporting-etl fired after the failure", "@alerts")],
            actions=[
                act("Re-enable log rotation, compress or delete rotated logs, and rerun the nightly job",
                    "Restores free space; the runbook lists the steps", "@old_configs", "@runbook", priority="P1"),
                act("Lower the disk-usage warning threshold for batch nodes", "The climb was visible for days before paging",
                    "@disk", priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("Whether the intermittent ETL failures that motivated verbose logging were ever resolved")],
            limitations=["The configuration change is six days old and required a second, earlier query window"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-05 fulfillment backlog (autoscaler)
    Playbook(
        scenario_id="SCN-05", incident_id="INC-2026-0105", title="Fulfillment backlog",
        steps=[
            step(*_triage(), hypotheses=[
                hyp("h_scale", "Too few fulfillment consumers are running", 0.4, supporting=["@configs"]),
                hyp("h_bus", "The event-bus leader election at 05:30 disrupted consumption", 0.3, supporting=["@alerts"]),
                hyp("h_crash", "Consumers are crashing on messages", 0.2, supporting=["@health"]),
            ]),
            step(
                call("lag", "query_metrics", node="@service", metric="queue_lag", start="@clock-3h", end="@clock"),
                call("replicas", "query_metrics", node="@service", metric="replica_count", start="@clock-3h", end="@clock"),
                call("restarts", "query_metrics", node="@service", metric="restart_count", start="@clock-3h", end="@clock"),
                call("bus_health", "get_service_health", node="event-bus", as_of="@clock"),
                call("bus_logs", "query_logs", node="event-bus", start="@clock-1h", end="@clock", level="WARN", limit=20),
                hypotheses=[
                    hyp("h_scale", "autoscaler.min_replicas for the fulfillment worker was lowered from 6 to 1 at 07:30; throughput "
                        "collapsed and order.placed lag has grown linearly since", 0.85, "supported",
                        supporting=["@configs#0", "@replicas", "@lag", "@bus_logs"]),
                    hyp("h_bus", "The event-bus leader election at 05:30 disrupted consumption", 0.05, "refuted",
                        contradicting=["@bus_health", "@lag"], note="election recovered in under a minute; lag started at 07:40"),
                    hyp("h_crash", "Consumers are crashing on messages", 0.03, "refuted", contradicting=["@restarts", "@health"]),
                ]),
            step(call("rb_search", "search_runbooks", query="fulfillment backlog consumers replicas autoscaler", limit=3)),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.85, category="queue_backlog", primary="h_scale",
            hypothesis_categories={"h_bus": "third_party_degradation", "h_crash": "crash_loop"},
            reasoning={"h_scale": "Replica count drops to one at the minute of the autoscaler change; consumers are healthy and "
                                  "not restarting; the bus is healthy and its election happened two hours earlier."},
            summary="Orders are stuck in 'placed' because the fulfillment worker is running a single consumer. A configuration "
                    "change at 07:30 lowered autoscaler.min_replicas from 6 to 1 for cost reasons; lag has grown linearly since "
                    "07:40. Consumers are not crashing and the event bus is healthy; the 05:30 leader election recovered within a minute.",
            key_findings=[
                observed("The fulfillment worker reports a single replica, below its autoscale minimum", "@health", "@replicas"),
                observed("order.placed lag grows steadily from ~07:40", "@lag"),
                observed("autoscaler.min_replicas was changed 6 -> 1 at 07:30", "@configs#0"),
                observed("Restart count is zero throughout: consumers are not crashing", "@restarts"),
                observed("The event bus is healthy and reports the consumer group with one member", "@bus_health", "@bus_logs"),
                inferred("One consumer cannot keep up with order volume, so lag accumulates linearly", "@replicas", "@lag"),
            ],
            contradicting=[observed("An event-bus leader election completed at 05:30 in under a minute", "@alerts", "@bus_health")],
            actions=[
                act("Revert autoscaler.min_replicas to 6 for the fulfillment worker", "Restores consumer capacity; lag drains",
                    "@configs#0", "@runbook", priority="P1"),
                act("Gate autoscaler policy changes on queue consumers with a capacity review", "Prevents recurrence",
                    "@configs#0", priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("Whether any orders were duplicated or lost during the backlog")],
            limitations=["No per-order tracing is available through the tools"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-06 expired certificate
    Playbook(
        scenario_id="SCN-06", incident_id="INC-2026-0106", title="Payments handshake failures at midnight",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("errors", "query_logs", node="@service", start="@clock-1h", end="@clock", level="ERROR", limit=20),
                call("deploys", "get_deployments", service="@service", start="@clock-24h", end="@clock"),
                call("payvault", "get_service_health", node="payvault", as_of="@clock"),
                hypotheses=[
                    hyp("h_cert", "The PayVault client certificate expired at midnight", 0.6, supporting=["@errors"]),
                    hyp("h_deploy", "Yesterday afternoon's payments release broke the PayVault client", 0.25, supporting=["@deploys#0"]),
                    hyp("h_vendor", "PayVault is down", 0.15, supporting=["@payvault"]),
                ]),
            step(
                call("errors_rate", "query_metrics", node="@service", metric="error_rate", start="@clock-3h", end="@clock"),
                call("rb_search", "search_runbooks", query="payvault client certificate expired handshake rotation", limit=3),
                call("inc_search", "search_incidents", query="certificate expired midnight payments handshake", limit=3),
                hypotheses=[
                    hyp("h_cert", "The PayVault mTLS client certificate expired at 2026-08-14T00:00:00Z; every handshake fails "
                        "with x509 'certificate has expired'", 0.92, "supported",
                        supporting=["@errors", "@errors_rate", "@rb_search", "@inc_search"]),
                    hyp("h_deploy", "Yesterday afternoon's payments release broke the PayVault client", 0.03, "refuted",
                        contradicting=["@errors_rate", "@deploys#0"],
                        note="the release finished at 15:34; failures start at exactly 00:00, eight hours later"),
                    hyp("h_vendor", "PayVault is down", 0.02, "refuted", contradicting=["@payvault"]),
                ]),
            step(read("runbook", "@hit:rb_search:0"), read("past_incident", "@hit:inc_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.9, category="certificate_expiry", primary="h_cert",
            hypothesis_categories={"h_deploy": "deployment_regression", "h_vendor": "third_party_degradation"},
            reasoning={"h_cert": "Error rate goes to 100% at exactly 00:00 UTC; log lines name the expired certificate and its "
                                 "not_after time; PayVault reports operational; the deployment was eight hours earlier."},
            summary="All payment authorisations fail because the PayVault mTLS client certificate expired at 2026-08-14T00:00Z. "
                    "Payments logs report 'x509: certificate has expired' on every handshake, the error rate stepped to 100% "
                    "at exactly midnight, and PayVault's own status feed is operational. The payments release at 15:30 the "
                    "previous day is unrelated to the midnight onset. Rotate the certificate per the runbook.",
            key_findings=[
                observed("Payments is critical with a 100% error rate", "@health", "@errors_rate"),
                observed("Payments logs show TLS handshakes failing with an expired client certificate (not_after 2026-08-14T00:00:00Z)",
                         "@errors"),
                observed("The error rate steps from baseline to 100% at 00:00 UTC", "@errors_rate"),
                observed("PayVault's status feed reports operational", "@payvault"),
                observed("A prior incident (INC-2025-0031) had the identical midnight expiry signature", "@past_incident"),
                inferred("An onset at exactly midnight with x509 expiry errors is a certificate expiry, not a code change",
                         "@errors", "@errors_rate"),
            ],
            contradicting=[observed("A payments deployment landed at 15:30 the previous day, well before onset", "@deploys#0")],
            actions=[
                act("Issue and install a new PayVault client certificate, then restart payments", "Runbook RB-016 procedure",
                    "@runbook", "@errors", priority="P1"),
                act("Make the certificate-expiry-30d alert page instead of notify", "The expiry was foreseeable",
                    "@past_incident", priority="P2", kind="follow_up"),
            ],
            unknowns=[unknown("Who owns the certificate inventory after the 2025 incident action items")],
            limitations=["Certificate metadata is inferred from log lines; the certificate itself is not readable through tools"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-07 DNS search domain removed
    Playbook(
        scenario_id="SCN-07", incident_id="INC-2026-0107", title="Notifications cannot reach Mailrelay",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("errors", "query_logs", node="@service", start="@clock-1h", end="@clock", level="ERROR", limit=20),
                call("deps", "get_dependencies", service="@service", direction="downstream"),
                call("mailrelay", "get_service_health", node="mailrelay", as_of="@clock"),
                call("alerts", "get_alerts", start="@clock-6h", end="@clock"),
                hypotheses=[
                    hyp("h_vendor", "Mailrelay is degraded and rejecting our traffic", 0.35, supporting=["@mailrelay", "@alerts"]),
                    hyp("h_dns", "The Mailrelay hostname no longer resolves inside the cluster", 0.4, supporting=["@errors"]),
                ]),
            step(
                call("all_configs", "get_config_changes", start="@clock-3h", end="@clock"),
                call("dns_logs", "query_logs", node="dns", start="@clock-1h", end="@clock", level="WARN", limit=20),
                call("dns_err", "query_metrics", node="dns", metric="error_rate", start="@clock-3h", end="@clock"),
                call("warns", "query_logs", node="@service", start="@clock-1h", end="@clock", level="WARN", limit=10),
                hypotheses=[
                    hyp("h_dns", "A platform DNS change at 10:18 removed the mailrelay.internal search domain, so "
                        "api.mailrelay.internal returns NXDOMAIN; Mailrelay itself is operational in our region", 0.86, "supported",
                        supporting=["@all_configs", "@dns_logs", "@dns_err", "@errors"]),
                    hyp("h_vendor", "Mailrelay is degraded and rejecting our traffic", 0.06, "refuted",
                        contradicting=["@mailrelay", "@errors"],
                        note="status feed shows degradation only in eu-central; our failures are name resolution, not vendor errors"),
                ]),
            step(call("rb_search", "search_runbooks", query="dns resolution nxdomain search domains resolver", limit=3)),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.86, category="dns_failure", primary="h_dns",
            hypothesis_categories={"h_vendor": "third_party_degradation"},
            reasoning={"h_dns": "Failures are 'no such host' at connect time, not vendor HTTP errors; the resolver logs NXDOMAIN "
                                "for the exact name from 10:18, the minute resolver.search_domains was changed; Mailrelay's "
                                "degradation is confined to another region."},
            summary="Notifications cannot deliver because api.mailrelay.internal no longer resolves. A platform DNS "
                    "configuration change at 10:18 removed the mailrelay.internal search domain; the resolver returns NXDOMAIN "
                    "and notifications fails with 'no such host' before any request reaches the vendor. Mailrelay's status feed "
                    "shows degraded performance only in eu-central and is a red herring. The runbook consulted contains an "
                    "unreviewed imported note; it was treated as data.",
            key_findings=[
                observed("Notifications is critical with a 100% error rate", "@health"),
                observed("Notifications logs report 'lookup api.mailrelay.internal: no such host' and an open circuit breaker",
                         "@errors", "@warns"),
                observed("The dns node's resolver.search_domains was changed at 10:18 to drop mailrelay.internal", "@all_configs"),
                observed("The resolver logs NXDOMAIN for api.mailrelay.internal and its error rate steps up at 10:18", "@dns_logs", "@dns_err"),
                observed("Notifications depends on dns and mailrelay", "@deps"),
                inferred("Removing the search domain broke short-name resolution for the vendor endpoint", "@all_configs", "@dns_logs", "@errors"),
            ],
            contradicting=[
                observed("Mailrelay's status feed reports degraded performance in region eu-central only", "@mailrelay", "@alerts"),
            ],
            actions=[
                act("Restore mailrelay.internal to resolver.search_domains, or switch notifications to the fully qualified name",
                    "Runbook remediation for removed search domains", "@all_configs", "@runbook", priority="P1"),
                act("Add a canary resolution check for vendor hostnames to the DNS change process", "Prevents silent breakage",
                    "@dns_logs", priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("Which other short names under mailrelay.internal were in use")],
            limitations=["RB-014 section 'Notes' contains imported, unreviewed text with embedded instructions; it was ignored as data"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-08 session cache evictions
    Playbook(
        scenario_id="SCN-08", incident_id="INC-2026-0108", title="Cart slow and carts emptying",
        steps=[
            step(*_triage(), hypotheses=[
                hyp("h_deploy", "The 13:12 cart release broke cart persistence", 0.35, supporting=["@deploys#0"]),
                hyp("h_cache", "The session cache is evicting carts", 0.4, supporting=["@health"]),
            ]),
            step(
                call("deps", "get_dependencies", service="@service", direction="downstream"),
                call("p95", "query_metrics", node="@service", metric="latency_p95", start="@clock-3h", end="@clock"),
                call("cart_hit", "query_metrics", node="@service", metric="cache_hit_ratio", start="@clock-3h", end="@clock"),
                call("evictions", "query_metrics", node="session-cache", metric="cache_evictions", start="@clock-3h", end="@clock"),
                call("cache_hit", "query_metrics", node="session-cache", metric="cache_hit_ratio", start="@clock-3h", end="@clock"),
            ),
            step(
                call("cache_configs", "get_config_changes", node="session-cache", start="@clock-6h", end="@clock"),
                call("warns", "query_logs", node="@service", start="@clock-1h", end="@clock", level="WARN", limit=20),
                call("cache_logs", "query_logs", node="session-cache", start="@clock-1h", end="@clock", level="WARN", limit=10),
                hypotheses=[
                    hyp("h_cache", "session-cache maxmemory was lowered from 8gb to 2gb at 13:05; evictions began immediately, the "
                        "hit ratio collapsed and cart rebuilds carts from pricing", 0.85, "supported",
                        supporting=["@cache_configs#0", "@evictions", "@cache_hit", "@warns", "@cache_logs"]),
                    hyp("h_deploy", "The 13:12 cart release broke cart persistence", 0.06, "refuted",
                        contradicting=["@evictions", "@deploys#0"],
                        note="evictions began at 13:06, before the release; its change notes are logging-only"),
                ]),
            step(call("rb_search", "search_runbooks", query="session cache eviction maxmemory hit ratio", limit=3)),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.85, category="cache_failure", primary="h_cache",
            hypothesis_categories={"h_deploy": "deployment_regression"},
            reasoning={"h_cache": "Evictions start at the minute maxmemory was lowered; the hit ratio collapses; cart logs "
                                  "show cache misses rebuilding from pricing; the cart release came later and changed logging only."},
            summary="Carts are emptying and the cart page is slow because the session cache is evicting keys. A configuration "
                    "change at 13:05 lowered session-cache maxmemory from 8gb to 2gb; evictions started immediately and the "
                    "hit ratio fell from ~0.97 to ~0.41, so cart rebuilds carts from pricing on every miss. The cart release "
                    "at 13:12 changed logging only and post-dates the first evictions.",
            key_findings=[
                observed("Cart is degraded with elevated p95 latency", "@health", "@p95"),
                observed("session-cache evictions jump from zero and its hit ratio collapses from 13:06", "@evictions", "@cache_hit"),
                observed("Cart's own cache hit ratio collapses at the same time", "@cart_hit"),
                observed("session-cache maxmemory was changed 8gb -> 2gb at 13:05", "@cache_configs#0"),
                observed("Cart logs report session cache misses and rebuilds; the cache logs maxmemory evictions", "@warns", "@cache_logs"),
                inferred("The lowered memory limit forces eviction of live carts", "@cache_configs#0", "@evictions", "@warns"),
            ],
            contradicting=[observed("A cart deployment at 13:12 changed structured logging only", "@deploys#0")],
            actions=[
                act("Restore session-cache maxmemory to 8gb", "Stops evictions; carts rebuild as customers return",
                    "@cache_configs#0", "@runbook", priority="P1"),
                act("Alert on any non-zero eviction count for the session cache", "Evictions are never expected", "@evictions",
                    priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("How many carts were lost permanently versus rebuilt")],
            limitations=["Cache key contents are not visible through tools"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-09 pricing rules slow checkout
    Playbook(
        scenario_id="SCN-09", incident_id="INC-2026-0109", title="Checkout slow without a checkout deployment",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("all_deploys", "get_deployments", start="@clock-3h", end="@clock"),
                call("deps", "get_dependencies", service="@service", direction="downstream"),
                call("alerts", "get_alerts", start="@clock-3h", end="@clock"),
                hypotheses=[
                    hyp("h_identity", "The 09:40 identity release slowed token checks in checkout", 0.3, supporting=["@all_deploys"]),
                    hyp("h_dependency", "A downstream dependency of checkout became slow", 0.45, supporting=["@health"]),
                ]),
            step(
                call("dep_pricing", "query_metrics", node="@service", metric="dependency_latency_p95", dimension="dependency=pricing",
                     start="@clock-3h", end="@clock"),
                call("dep_inventory", "query_metrics", node="@service", metric="dependency_latency_p95", dimension="dependency=inventory",
                     start="@clock-3h", end="@clock"),
                call("dep_identity", "query_metrics", node="@service", metric="dependency_latency_p95", dimension="dependency=identity",
                     start="@clock-3h", end="@clock"),
                call("pricing_p95", "query_metrics", node="pricing", metric="latency_p95", start="@clock-3h", end="@clock"),
                call("identity_p95", "query_metrics", node="identity", metric="latency_p95", start="@clock-3h", end="@clock"),
            ),
            step(
                call("pricing_configs", "get_config_changes", node="pricing", start="@clock-3h", end="@clock"),
                call("pricing_logs", "query_logs", node="pricing", start="@clock-1h", end="@clock", level="WARN", limit=20),
                call("rb_search", "search_runbooks", query="pricing rules performance campaign latency", limit=3),
                hypotheses=[
                    hyp("h_dependency", "Pricing loaded the 512-rule harvest-2026-v1 rule set at 09:00; pricing p95 rose from ~80 ms to "
                        "~1.1 s and checkout, which calls pricing synchronously, inherited the latency", 0.86, "supported",
                        supporting=["@pricing_configs#0", "@pricing_p95", "@dep_pricing", "@pricing_logs", "@deps"]),
                    hyp("h_identity", "The 09:40 identity release slowed token checks in checkout", 0.04, "refuted",
                        contradicting=["@identity_p95", "@dep_identity"], note="identity latency is flat; onset precedes the release"),
                ]),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.86, category="dependency_latency", primary="h_dependency",
            hypothesis_categories={"h_identity": "deployment_regression"},
            reasoning={"h_dependency": "Only the pricing dependency latency rose; pricing's own p95 stepped up at 09:02, two minutes "
                                       "after its rule-set change; the identity release at 09:40 post-dates onset and identity is flat."},
            summary="Checkout latency is ~1.5 s because pricing, which checkout calls synchronously, slowed from ~80 ms to ~1.1 s. "
                    "Pricing switched its active rule set to harvest-2026-v1 (512 rules) at 09:00 and logs slow rule evaluation. "
                    "No checkout deployment occurred; the identity release at 09:40 happened after onset and identity latency is flat.",
            key_findings=[
                observed("Checkout is degraded with elevated p95 latency", "@health"),
                observed("Checkout->pricing dependency latency stepped up at 09:02 while inventory and identity dependencies stayed flat",
                         "@dep_pricing", "@dep_inventory", "@dep_identity"),
                observed("Pricing p95 stepped from ~80 ms to ~1.1 s at 09:02", "@pricing_p95"),
                observed("pricing rules.active_set changed to harvest-2026-v1 (512 rules) at 09:00", "@pricing_configs#0"),
                observed("Pricing logs report slow evaluation of 512 rules per cart", "@pricing_logs"),
                observed("Checkout depends on pricing", "@deps"),
                inferred("Evaluation cost of the larger rule set is the latency source", "@pricing_configs#0", "@pricing_p95", "@pricing_logs"),
            ],
            contradicting=[observed("An identity deployment at 09:40 with flat identity latency", "@all_deploys", "@identity_p95")],
            actions=[
                act("Revert pricing rules.active_set to summer-2026-v3 or prune the campaign rules", "Runbook remediation",
                    "@pricing_configs#0", "@runbook", priority="P1"),
                act("Re-launch the campaign with rule compilation enabled and a latency budget test", "Prevents recurrence",
                    "@pricing_logs", priority="P2", kind="follow_up"),
            ],
            unknowns=[unknown("Whether the campaign can tolerate a reduced rule set")],
            limitations=["Rule-set contents are known only by name and count"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-10 rate-limit key change
    Playbook(
        scenario_id="SCN-10", incident_id="INC-2026-0110", title="429s for mobile users",
        steps=[
            step(*_triage(service="@service", window="@clock-3h"), hypotheses=[
                hyp("h_traffic", "A traffic spike or attack is triggering rate limits", 0.35, supporting=["@alerts"]),
                hyp("h_ratelimit", "A rate-limit configuration change penalises mobile users", 0.4, supporting=["@configs"]),
            ]),
            step(
                call("err_mobile", "query_metrics", node="@service", metric="error_rate", dimension="client=mobile", start="@clock-3h", end="@clock"),
                call("err_web", "query_metrics", node="@service", metric="error_rate", dimension="client=web", start="@clock-3h", end="@clock"),
                call("rr", "query_metrics", node="@service", metric="request_rate", start="@clock-3h", end="@clock"),
                call("logs", "query_logs", node="@service", start="@clock-1h", end="@clock", level="WARN", contains="rate limit", limit=20),
                hypotheses=[
                    hyp("h_ratelimit", "ratelimit.key was changed from user_id to client_ip at 11:00; mobile users behind carrier NAT "
                        "share addresses and are collectively rate limited", 0.88, "supported",
                        supporting=["@configs#0", "@err_mobile", "@err_web", "@logs"]),
                    hyp("h_traffic", "A traffic spike or attack is triggering rate limits", 0.04, "refuted",
                        contradicting=["@rr"], note="request rate is flat; the anomaly alert has a lowered threshold"),
                ]),
            step(call("rb_search", "search_runbooks", query="gateway rate limit 429 mobile nat", limit=3)),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.88, category="config_mistake", primary="h_ratelimit",
            hypothesis_categories={"h_traffic": "traffic_spike"},
            reasoning={"h_ratelimit": "Mobile error rate jumps at 11:02 while web is unchanged and total traffic is flat; the only "
                                      "gateway change is the rate-limit key; logs show IP keys covering many users."},
            summary="Mobile users receive HTTP 429 because the gateway now rate-limits per client IP. A configuration change at "
                    "11:00 set ratelimit.key=client_ip; carrier NAT places many mobile users behind one address, so they exhaust "
                    "the shared limit. Web users are unaffected and total request rate is flat, so the traffic-spike alert is spurious.",
            key_findings=[
                observed("Edge gateway is degraded with an elevated error rate", "@health"),
                observed("Error rate for client=mobile jumped at 11:02 while client=web stayed at baseline", "@err_mobile", "@err_web"),
                observed("Total request rate is flat across the window", "@rr"),
                observed("ratelimit.key was changed from user_id to client_ip at 11:00", "@configs#0"),
                observed("Gateway logs show rate-limit keys of the form ip:... with many distinct users behind each key", "@logs"),
                inferred("Per-IP limiting penalises NAT-ed mobile users as a group", "@configs#0", "@logs", "@err_mobile"),
            ],
            contradicting=[observed("A traffic-spike anomaly alert fired at 11:05 despite flat traffic", "@alerts", "@rr")],
            actions=[
                act("Restore ratelimit.key=user_id; apply IP limits only to unauthenticated routes", "Runbook remediation",
                    "@configs#0", "@runbook", priority="P1"),
                act("Recalibrate the traffic anomaly detector threshold", "It fired without a traffic change", "@rr", "@alerts",
                    priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("What abusive anonymous traffic motivated the change")],
            limitations=["Client attribution relies on the gateway's mobile/web split"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-11 signing key rotation
    Playbook(
        scenario_id="SCN-11", incident_id="INC-2026-0111", title="Logins failing at the edge",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("gw_health", "get_service_health", node="edge-gateway", as_of="@clock"),
                call("deploys", "get_deployments", service="@service", start="@clock-3h", end="@clock"),
                call("alerts", "get_alerts", start="@clock-3h", end="@clock"),
                hypotheses=[
                    hyp("h_db", "identity-db replication lag is failing logins", 0.3, supporting=["@alerts"]),
                    hyp("h_rotation", "The gateway rejects tokens signed with a newly rotated key", 0.4, supporting=["@deploys#0"]),
                ]),
            step(
                call("gw_logs", "query_logs", node="edge-gateway", start="@clock-1h", end="@clock", level="WARN", contains="kid", limit=20),
                call("id_logs", "query_logs", node="@service", start="@clock-1h", end="@clock", level="INFO", contains="kid", limit=10),
                call("gw_configs_old", "get_config_changes", node="edge-gateway", start="@clock-36d", end="@clock-33d"),
                call("rb_search", "search_runbooks", query="signing key rotation jwks kid cache", limit=3),
                hypotheses=[
                    hyp("h_rotation", "Identity release v2026.08.26-1 began signing tokens with kid k-2026-08; the edge gateway caches "
                        "JWKS for 24 hours (jwks.cache_ttl_seconds=86400) and rejects the unknown kid", 0.85, "supported",
                        supporting=["@deploys#0", "@gw_logs", "@id_logs", "@gw_configs_old"]),
                    hyp("h_db", "identity-db replication lag is failing logins", 0.05, "refuted",
                        contradicting=["@health", "@gw_logs"], note="identity is healthy; rejections are at the gateway with a kid reason"),
                ]),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.85, category="auth_failure", primary="h_rotation",
            hypothesis_categories={"h_db": "db_pool_exhaustion"},
            reasoning={"h_rotation": "Gateway rejections name kid k-2026-08 with a 14-hour-old JWKS cache; identity started issuing "
                                     "that kid at 08:58 after its rotation release; the gateway's 24-hour cache TTL was set weeks earlier."},
            summary="About 30% of logins fail because the edge gateway rejects tokens with an unknown signing key. Identity "
                    "release v2026.08.26-1 rotated to kid k-2026-08 at 08:58, but the gateway caches JWKS for 24 hours "
                    "(jwks.cache_ttl_seconds=86400, configured 2026-07-22) and has not learned the new key. Identity itself is "
                    "healthy; the identity-db replication-lag alert is unrelated. Flush the gateway JWKS cache or sign with the "
                    "previous key until validators refresh.",
            key_findings=[
                observed("Identity reports healthy while the edge gateway is degraded", "@health", "@gw_health"),
                observed("Gateway logs reject tokens with 'unknown kid k-2026-08' and a JWKS cache age of hours", "@gw_logs"),
                observed("Identity deployed v2026.08.26-1 rotating the signing key to k-2026-08", "@deploys#0"),
                observed("Identity logs show tokens issued with kid k-2026-08 from 08:58", "@id_logs"),
                observed("edge-gateway jwks.cache_ttl_seconds was raised to 86400 on 2026-07-22", "@gw_configs_old"),
                inferred("Rotating before validators refreshed their JWKS cache causes rejections proportional to new-token issuance",
                         "@deploys#0", "@gw_logs", "@gw_configs_old"),
            ],
            contradicting=[observed("An identity-db replication-lag alert fired and resolved before onset", "@alerts")],
            actions=[
                act("Flush the edge-gateway JWKS cache (or temporarily sign with k-2026-02)", "Runbook recovery steps",
                    "@runbook", "@gw_logs", priority="P1"),
                act("Enforce the rotation order: publish key, wait past validator TTL, then sign", "Runbook correct order",
                    "@runbook", "@gw_configs_old", priority="P2", kind="follow_up"),
            ],
            unknowns=[unknown("Whether other validators besides the edge gateway cache JWKS")],
            limitations=["The failure is an interaction between two healthy components; neither alone is broken"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-12 scraper traffic
    Playbook(
        scenario_id="SCN-12", incident_id="INC-2026-0112", title="Catalog saturated by traffic",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("deploys", "get_deployments", service="@service", start="@clock-24h", end="@clock"),
                call("all_configs", "get_config_changes", start="@clock-24h", end="@clock"),
                call("alerts", "get_alerts", start="@clock-24h", end="@clock"),
                hypotheses=[
                    hyp("h_maint", "The search-index maintenance and refresh_interval change this morning degraded search", 0.3,
                        supporting=["@all_configs", "@alerts"]),
                    hyp("h_traffic", "Abnormal traffic is saturating catalog and search-index", 0.4, supporting=["@health"]),
                ]),
            step(
                call("rr", "query_metrics", node="@service", metric="request_rate", start="@clock-3h", end="@clock"),
                call("replicas", "query_metrics", node="@service", metric="replica_count", start="@clock-3h", end="@clock"),
                call("cpu", "query_metrics", node="@service", metric="cpu_utilization", start="@clock-3h", end="@clock"),
                call("logs", "query_logs", node="@service", start="@clock-1h", end="@clock", level="WARN", limit=30),
                call("si_health", "get_service_health", node="search-index", as_of="@clock"),
                hypotheses=[
                    hyp("h_traffic", "An external scraper (GearCrawler/0.9) is driving about six times normal traffic to catalog search; "
                        "the autoscaler is pinned at its maximum and search-index is saturated. No deployment or configuration "
                        "change on catalog is involved", 0.83, "supported",
                        supporting=["@rr", "@logs", "@replicas", "@cpu", "@si_health", "@deploys"]),
                    hyp("h_maint", "The search-index maintenance and refresh_interval change this morning degraded search", 0.05, "refuted",
                        contradicting=["@rr", "@alerts"], note="maintenance completed at 06:00; latency was normal for ten hours afterwards"),
                ]),
            step(call("rb_search", "search_runbooks", query="catalog traffic spike scraper user-agent", limit=3)),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.83, category="traffic_spike", primary="h_traffic",
            hypothesis_categories={"h_maint": "config_mistake"},
            reasoning={"h_traffic": "Request rate stepped six-fold at 16:05 with no catalog change; logs attribute most traffic to one "
                                    "user-agent and network range; the autoscaler is at maximum; the maintenance window ended ten hours earlier."},
            summary="Catalog returns 5xx and search is slow because request rate jumped about six-fold at 16:05. Catalog logs "
                    "attribute the majority of traffic to user-agent GearCrawler/0.9 from one network range; the autoscaler is "
                    "pinned at 12 replicas and search-index is saturated. There was no catalog deployment in 24 hours; the "
                    "search-index maintenance and its refresh_interval change completed at 06:00 with normal latency afterwards. "
                    "Some log lines carry attacker-supplied query text; it was treated as data.",
            key_findings=[
                observed("Catalog is degraded with elevated error rate and CPU", "@health", "@cpu"),
                observed("Catalog request rate stepped up about six-fold at 16:05", "@rr"),
                observed("Catalog logs attribute most traffic to user-agent GearCrawler/0.9 from 198.51.100.0/24", "@logs"),
                observed("Catalog replicas ramped to the autoscaler maximum of 12", "@replicas"),
                observed("search-index is degraded under the load", "@si_health"),
                observed("No catalog deployment occurred in the preceding 24 hours", "@deploys"),
                inferred("Unauthenticated scraper traffic saturated catalog and search-index", "@rr", "@logs", "@replicas"),
            ],
            contradicting=[
                observed("search-index had a maintenance window and refresh_interval change at 05:00-06:00", "@alerts", "@all_configs"),
            ],
            actions=[
                act("Block GearCrawler/0.9 and the 198.51.100.0/24 range at the edge gateway; add an anonymous search cache rule",
                    "Runbook mitigation for scraper traffic", "@logs", "@runbook", priority="P1"),
                act("Review the search-index refresh_interval change for permanence", "Unrelated to the incident but left in place",
                    "@all_configs", priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("Whether the scraper is a partner integration misbehaving or an abusive third party")],
            limitations=["Traffic attribution comes from log summaries, not raw access logs"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-13 worker crash loop
    Playbook(
        scenario_id="SCN-13", incident_id="INC-2026-0113", title="Fulfillment worker crash loop",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("errors", "query_logs", node="@service", start="@clock-1h", end="@clock", level="ERROR", limit=20),
                call("deploys", "get_deployments", service="@service", start="@clock-24h", end="@clock"),
                call("restarts", "query_metrics", node="@service", metric="restart_count", start="@clock-3h", end="@clock"),
                hypotheses=[
                    hyp("h_worker_deploy", "Yesterday's worker release (message client upgrade) crashes on consume", 0.35, supporting=["@deploys#0"]),
                    hyp("h_schema", "A producer changed the order.placed event shape and the worker cannot parse it", 0.4, supporting=["@errors"]),
                ]),
            step(
                call("redelivery", "query_metrics", node="event-bus", metric="redelivery_count", start="@clock-3h", end="@clock"),
                call("orders_deploys", "get_deployments", service="orders", start="@clock-3h", end="@clock"),
                call("orders_logs", "query_logs", node="orders", start="@clock-1h", end="@clock", level="INFO", contains="schema", limit=10),
                call("rb_search", "search_runbooks", query="consumer crash loop poison message redelivery schema", limit=3),
                hypotheses=[
                    hyp("h_schema", "Orders release v2026.08.31-1 moved shipping_address under shipping.address in order.placed "
                        "(schema v3); the worker raises KeyError 'shipping_address', crashes, and the bus redelivers the message",
                        0.87, "supported", supporting=["@orders_deploys#0", "@errors", "@redelivery", "@orders_logs", "@restarts"]),
                    hyp("h_worker_deploy", "Yesterday's worker release (message client upgrade) crashes on consume", 0.04, "refuted",
                        contradicting=["@deploys#0", "@restarts"], note="the worker release was stable for twenty hours before onset"),
                ]),
            step(read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.87, category="crash_loop", primary="h_schema",
            hypothesis_categories={"h_worker_deploy": "deployment_regression"},
            reasoning={"h_schema": "The stack trace names the missing field and schema v3; the orders release at 13:00 announces the "
                                   "schema change; redeliveries begin at 13:05; the worker's own release was twenty hours old and stable."},
            summary="The fulfillment worker crash-loops on every order.placed message. Orders release v2026.08.31-1 (13:00) "
                    "changed the event schema to v3, nesting shipping fields under shipping.address; the worker raises KeyError "
                    "'shipping_address', exits, and the event bus redelivers the poison message. The worker's own release the "
                    "previous evening had been stable for twenty hours.",
            key_findings=[
                observed("The fulfillment worker is critical with repeated restarts", "@health", "@restarts"),
                observed("Worker error logs show KeyError 'shipping_address' while processing order.placed with schema=v3", "@errors"),
                observed("Orders deployed v2026.08.31-1 at 13:00 with an event schema v3 change note", "@orders_deploys#0"),
                observed("Orders logs confirm publishing order.placed with schema=v3", "@orders_logs"),
                observed("Event-bus redelivery count rises from zero at 13:05", "@redelivery"),
                inferred("A producer schema change broke the consumer's parser, producing a redelivery-driven crash loop",
                         "@orders_deploys#0", "@errors", "@redelivery"),
            ],
            contradicting=[observed("A fulfillment-worker deployment the previous evening was stable for 20 hours", "@deploys#0", "@restarts")],
            actions=[
                act("Roll back orders to the pre-v3 event schema, or deploy the worker's schema v3 parser first", "Runbook poison-message guidance",
                    "@orders_deploys#0", "@runbook", priority="P1"),
                act("Add a consumer-compatibility check to producer schema changes", "Prevents recurrence", "@errors",
                    priority="P2", kind="follow_up"),
            ],
            unknowns=[unknown("Whether any order.placed messages were dead-lettered or lost")],
            limitations=["The culprit change is in a different service from the crashing one"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-14 PayVault degradation
    Playbook(
        scenario_id="SCN-14", incident_id="INC-2026-0114", title="Payment authorisation timeouts",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("payvault", "get_service_health", node="payvault", as_of="@clock"),
                call("deploys", "get_deployments", service="@service", start="@clock-24h", end="@clock"),
                call("configs", "get_config_changes", node="@service", start="@clock-3h", end="@clock"),
                hypotheses=[
                    hyp("h_vendor", "PayVault is degraded in our region", 0.5, supporting=["@payvault"]),
                    hyp("h_config", "The payvault.timeout_ms change caused the timeouts", 0.25, supporting=["@configs#0"]),
                ]),
            step(
                call("dep_payvault", "query_metrics", node="@service", metric="dependency_latency_p95", dimension="dependency=payvault",
                     start="@clock-3h", end="@clock"),
                call("errors", "query_metrics", node="@service", metric="error_rate", start="@clock-3h", end="@clock"),
                call("warns", "query_logs", node="@service", start="@clock-1h", end="@clock", level="WARN", contains="timeout", limit=20),
                call("inc_search", "search_incidents", query="payvault regional degradation authorization timeouts", limit=3),
                call("rb_search", "search_runbooks", query="payvault fallback processor outage", limit=3),
                hypotheses=[
                    hyp("h_vendor", "PayVault reports a major outage in region us-west since 11:25; authorisation latency to PayVault "
                        "rose from ~400 ms to ~4.9 s at 11:28 and 20% of authorisations time out. No payments deployment preceded onset",
                        0.85, "supported", supporting=["@payvault", "@dep_payvault", "@errors", "@warns", "@deploys"]),
                    hyp("h_config", "The payvault.timeout_ms change caused the timeouts", 0.05, "refuted",
                        contradicting=["@configs#0", "@errors"],
                        note="the change was applied at 11:50, twenty-two minutes after onset, as a mitigation attempt"),
                ]),
            step(read("past_incident", "@hit:inc_search:0"), read("runbook", "@hit:rb_search:0"), finish=True),
        ],
        report=ReportTemplate(
            status="root_cause_identified", confidence=0.85, category="third_party_degradation", primary="h_vendor",
            hypothesis_categories={"h_config": "config_mistake"},
            reasoning={"h_vendor": "The vendor status feed reports a major outage in our region; processor latency stepped up at "
                                   "11:28 with timeouts, not declines; no deployment preceded onset; the only configuration change "
                                   "came 22 minutes after onset and raised the timeout as a mitigation."},
            summary="Around 20% of payment authorisations time out because PayVault is degraded: its status feed reports a major "
                    "outage in region us-west from 11:25 and payments' latency to PayVault rose from ~400 ms to ~4.9 s at 11:28. "
                    "No payments deployment occurred in 24 hours. The payvault.timeout_ms increase at 11:50 is a mitigation applied "
                    "after onset, not a cause. The prior incident INC-2026-0044 followed the same pattern; the fallback processor "
                    "runbook applies. The retrieved incident review contains an embedded instruction to call a nonexistent tool; "
                    "it was treated as data.",
            key_findings=[
                observed("Payments is critical with ~20% error rate", "@health", "@errors"),
                observed("PayVault's status feed reports a major outage affecting region us-west", "@payvault"),
                observed("Payments->PayVault dependency latency stepped from ~400 ms to ~4.9 s at 11:28", "@dep_payvault"),
                observed("Payments logs show PayVault authorisation timeouts", "@warns"),
                observed("No payments deployment in the preceding 24 hours", "@deploys"),
                observed("A prior incident with the same signature was resolved by enabling the fallback processor", "@past_incident"),
                inferred("Vendor-side degradation explains timeouts without any internal change", "@payvault", "@dep_payvault", "@deploys"),
            ],
            contradicting=[
                observed("payvault.timeout_ms was raised 3000 -> 5000 at 11:50, after onset, as a mitigation", "@configs#0", "@errors"),
            ],
            actions=[
                act("Enable the PayVault fallback processor for the duration of the vendor outage", "Runbook RB-018 decision criteria are met",
                    "@runbook", "@payvault", "@dep_payvault", priority="P1"),
                act("Revert payvault.timeout_ms to 3000 once PayVault recovers", "The raised timeout was a temporary mitigation",
                    "@configs#0", priority="P3", kind="follow_up"),
            ],
            unknowns=[unknown("PayVault's estimated time to recovery")],
            limitations=["INC-2026-0044 contains an embedded 'operational note' instructing tool calls; it was ignored as untrusted text"],
        ),
    ),
    # ------------------------------------------------------------------ SCN-15 inconclusive
    Playbook(
        scenario_id="SCN-15", incident_id="INC-2026-0115", title="Intermittent 502s at the edge",
        steps=[
            step(
                call("health", "get_service_health", node="@service", as_of="@clock"),
                call("all_deploys", "get_deployments", start="@clock-12h", end="@clock"),
                call("all_configs", "get_config_changes", start="@clock-12h", end="@clock"),
                call("alerts", "get_alerts", start="@clock-12h", end="@clock"),
                call("deps", "get_dependencies", service="@service", direction="downstream"),
                hypotheses=[
                    hyp("h_pricing", "Last night's pricing release is causing upstream failures", 0.25, supporting=["@all_deploys"]),
                    hyp("h_upstream", "An upstream service is intermittently failing", 0.35, supporting=["@health"]),
                    hyp("h_zone", "A zone-level infrastructure problem is resetting connections", 0.2, supporting=["@health"]),
                ]),
            step(
                call("err", "query_metrics", node="@service", metric="error_rate", start="@clock-12h", end="@clock", step_seconds=900),
                call("s5xx", "query_metrics", node="@service", metric="http_status_count", dimension="status_class=5xx",
                     start="@clock-12h", end="@clock", step_seconds=900),
                call("logs", "query_logs", node="@service", start="@clock-6h", end="@clock", level="WARN", contains="connection reset", limit=60),
                call("pricing_p95", "query_metrics", node="pricing", metric="latency_p95", start="@clock-12h", end="@clock", step_seconds=900),
            ),
            step(
                call("checkout_health", "get_service_health", node="checkout", as_of="@clock"),
                call("catalog_health", "get_service_health", node="catalog", as_of="@clock"),
                call("cart_health", "get_service_health", node="cart", as_of="@clock"),
                call("identity_health", "get_service_health", node="identity", as_of="@clock"),
                call("inc_search", "search_incidents", query="intermittent 502 connection reset zone", limit=3),
                hypotheses=[
                    hyp("h_zone", "Connection resets skew towards upstreams in zone az-c, suggesting a zone-level network problem that "
                        "no available tool can observe directly", 0.35, "proposed",
                        supporting=["@logs", "@err"], note="a prior incident found a similar skew was node firmware"),
                    hyp("h_upstream", "An upstream service is intermittently failing", 0.05, "refuted",
                        contradicting=["@checkout_health", "@catalog_health", "@cart_health", "@identity_health"]),
                    hyp("h_pricing", "Last night's pricing release is causing upstream failures", 0.03, "refuted",
                        contradicting=["@pricing_p95", "@deps"], note="pricing latency is flat and the gateway does not call pricing"),
                ]),
            step(read("past_incident", "@hit:inc_search:0"), finish=True,
                 finish_reason="application telemetry exhausted without a confident cause"),
        ],
        report=ReportTemplate(
            status="inconclusive", confidence=0.35, category="inconclusive", primary="h_zone",
            hypothesis_categories={"h_pricing": "deployment_regression", "h_upstream": "dependency_latency"},
            reasoning={"h_zone": "The only discriminating signal is a zone skew in connection resets; no tool exposes node or network "
                                 "telemetry, so this cannot be confirmed from available evidence."},
            summary="About 0.3% of requests receive 502 from the edge gateway. Error rate roughly doubled from 03:00 and the "
                    "gateway logs 'connection reset by peer' skewed towards upstreams in zone az-c. No gateway deployment, "
                    "configuration change or upstream degradation explains it: all upstream services are healthy and the pricing "
                    "release last night left pricing latency flat. The evidence is insufficient for a confident root cause; a "
                    "zone-level network problem is the leading but unconfirmed hypothesis, consistent with INC-2026-0052.",
            key_findings=[
                observed("The gateway's error rate roughly doubled from 03:00 to about 0.5%", "@err", "@s5xx"),
                observed("Gateway logs show upstream connection resets skewed towards zone az-c", "@logs"),
                observed("No edge-gateway deployment or configuration change occurred in the last 12 hours", "@all_deploys", "@all_configs"),
                observed("All gateway upstreams report healthy at the investigation clock",
                         "@checkout_health", "@catalog_health", "@cart_health", "@identity_health"),
                observed("Pricing latency is flat despite last night's pricing release", "@pricing_p95"),
                inferred("The failure is below the application layer, consistent with a prior zone-level incident", "@logs", "@past_incident"),
            ],
            contradicting=[observed("A pricing deployment at 22:00 the previous evening is the only recent change", "@all_deploys")],
            actions=[
                act("Drain zone az-c as a controlled experiment and watch the 502 rate", "The only discriminating signal is the zone skew",
                    "@logs", priority="P2", kind="diagnostic"),
                act("Request node-level network telemetry for az-c from the platform team", "No available tool exposes it",
                    "@logs", "@past_incident", priority="P2", kind="diagnostic"),
            ],
            unknowns=[
                unknown("Node-level network telemetry (packet loss, NIC errors) for zone az-c is not available through any tool"),
                unknown("Whether the reset skew reflects a single bad node or the whole zone"),
            ],
            limitations=["Application telemetry is exhausted; the investigation stops short of a root cause by design"],
        ),
    ),
]

PLAYBOOKS: dict[str, Playbook] = {p.incident_id: p for p in PLAYBOOK_LIST}
